"""`ffp schedule`: run `ffp slate` then `ffp push` on game days (S11.3, ADR 0006).

The rules (`next_run`), all in US Eastern time, the NFL's own clock:

- A game day is an Eastern date with at least one kickoff still ahead.
- On a game day there is a run every 30 minutes on :00 and :30 from 08:00 until the day's last
  kickoff, plus one 10 minutes before each kickoff window. A window is the kickoffs within 30
  minutes of its first one, so Sunday's 16:05 and 16:25 Games share a 15:55 run.
- A :00/:30 run 10 minutes or less before or after a window's run is dropped, so runs stay more
  than 10 minutes apart: the site answers 429 to a push under 5 minutes after the last one it
  accepted (S11.4). Sunday's early window gets 12:30, 12:50, 13:30; Sunday Night Football at 20:20
  gets 19:30 and 20:10.
- The loop also starts a run no sooner than 5 minutes after the last successful one ended, in case
  a long run ended just before a slot.
- After the day's last kickoff there is nothing left to price (`ffp slate` leaves out Games that
  have kicked off), so nothing runs until the next game day.

Kickoffs come from the nflverse schedule `ffp` already loads (`ffp.players.load_schedule`): it has
the whole season, so the scheduler knows next week's game days without asking ESPN, and nflreadpy
caches it. It is reloaded at least every 6 hours to pick up flexed Games.

A run that fails is logged and reported to `Alerts`; the loop then waits for the next slot, never
retrying at once. A run that overruns a slot skips the slots it covered.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, time, timedelta
from itertools import groupby
from pathlib import Path
from zoneinfo import ZoneInfo

from ffp import push

log = logging.getLogger("ffp.schedule")

EASTERN = ZoneInfo("America/New_York")
DAY_START = time(8, 0)  # Eastern; the first run of a game day
EVERY = timedelta(minutes=30)
LEAD = timedelta(minutes=10)  # a window's run, before its first kickoff
WINDOW = timedelta(minutes=30)  # kickoffs this close to a window's first share its run
IDLE_RECHECK = timedelta(days=1)  # no kickoffs ahead (off-season): look again then
RELOAD_EVERY = timedelta(hours=6)  # the longest the loop goes without reloading the schedule
SCHEDULE_RETRY = timedelta(hours=1)  # after the schedule failed to load
MIN_SPACING = timedelta(minutes=5)  # the site's least time between accepted pushes (429 otherwise)
MAX_SLEEP = timedelta(minutes=5)  # sleep in steps, so a machine that was suspended wakes on time
SLATE_TIMEOUT = timedelta(minutes=10)  # a slate takes about 1 minute; its push must stay young (push.MAX_AGE)
PUSH_TIMEOUT = timedelta(minutes=3)
SLATE_FILE = "expected-points.json"  # what `ffp slate --output-dir DIR` writes in DIR


def _aware(value: datetime | str) -> datetime:
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    if when.tzinfo is None:
        raise ValueError(f"kickoff {value!r} must carry a timezone")
    return when.astimezone(UTC)


def kickoff_windows(kickoffs: Iterable[datetime]) -> list[datetime]:
    """The first kickoff of each window: kickoffs within WINDOW of it share it."""
    starts: list[datetime] = []
    for kickoff in sorted(set(kickoffs)):
        if not starts or kickoff - starts[-1] > WINDOW:
            starts.append(kickoff)
    return starts


def day_runs(kickoffs: Iterable[datetime]) -> list[datetime]:
    """Every run of one game day, in UTC, from that day's kickoffs."""
    kickoffs = sorted(kickoffs)
    day = kickoffs[0].astimezone(EASTERN).date()
    window_runs = [start - LEAD for start in kickoff_windows(kickoffs)]
    grid, slot = [], datetime.combine(day, DAY_START, EASTERN)
    while slot < kickoffs[-1]:
        utc = slot.astimezone(UTC)
        if not any(abs(run - utc) <= LEAD for run in window_runs):
            grid.append(utc)
        slot = (utc + EVERY).astimezone(EASTERN)  # step in UTC: a daylight-saving day stays on :00/:30
    return sorted(set(grid) | set(window_runs))


def next_run(now: datetime, kickoffs: Iterable[datetime | str]) -> datetime:
    """The first run after `now` (aware) for these kickoffs (aware datetimes or ISO strings), in UTC.
    With no game day ahead: `now` + IDLE_RECHECK, to look again then."""
    if now.tzinfo is None:
        raise ValueError("now must carry a timezone, e.g. datetime.now(UTC)")
    now = now.astimezone(UTC)
    ahead = sorted(k for k in map(_aware, kickoffs) if k > now)
    for _, day in groupby(ahead, key=lambda k: k.astimezone(EASTERN).date()):
        # the day's earlier kickoffs (already past) don't matter: only its runs after now
        later = [run for run in day_runs(list(day)) if run > now]
        if later:
            return later[0]
    return now + IDLE_RECHECK


# --- the loop ------------------------------------------------------------------------------------


class RunFailed(Exception):
    """A run's step (slate or push) failed; `message` says how."""

    def __init__(self, step: str, message: str):
        super().__init__(f"{step}: {message}")
        self.step, self.message = step, message


class Alerts:
    """What the loop reports after each run. Sprint 12 subclasses this to send ntfy alerts (no
    successful push for 2 hours on a game day; ADR 0006, PLAN.md "Alerts"); this one only logs."""

    def run_succeeded(self, at: datetime) -> None:
        log.info("run for %s pushed", _show(at))

    def run_failed(self, at: datetime, step: str, message: str) -> None:
        log.error("run for %s failed at %s: %s; waiting for the next slot", _show(at), step, message)


def serve(
    *,
    load_kickoffs: Callable[[datetime], Iterable[datetime | str]],
    run_once: Callable[[datetime], None],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None],
    alerts: Alerts | None = None,
    max_runs: int | None = None,
) -> None:
    """Run `run_once(slot)` at each `next_run` slot, forever (or `max_runs` times)."""
    alerts = alerts or Alerts()
    runs, last_success = 0, None
    while max_runs is None or runs < max_runs:
        now = clock()
        try:
            kickoffs = list(load_kickoffs(now))
        except Exception as error:  # noqa: BLE001 - network or nflverse trouble: try later, don't die
            log.warning("could not load the schedule (%s); trying again in %s", error, SCHEDULE_RETRY)
            _sleep_until(now + SCHEDULE_RETRY, clock, sleep)
            continue
        # next_run is strictly after its argument: a slot exactly MIN_SPACING after the last success counts
        earliest = now if last_success is None else max(now, last_success + MIN_SPACING - timedelta(seconds=1))
        at = next_run(earliest, kickoffs)
        if at - now > RELOAD_EVERY:
            log.info("next run %s; reloading the schedule in %s", _show(at), RELOAD_EVERY)
            _sleep_until(now + RELOAD_EVERY, clock, sleep)
            continue
        log.info("next run %s", _show(at))
        _sleep_until(at, clock, sleep)
        runs += 1
        try:
            run_once(at)
        except RunFailed as failure:
            alerts.run_failed(at, failure.step, failure.message)
        except Exception as error:  # noqa: BLE001 - one bad run must not stop the scheduler
            alerts.run_failed(at, "run", repr(error))
        else:
            last_success = clock()
            alerts.run_succeeded(at)


def _sleep_until(when: datetime, clock: Callable[[], datetime], sleep: Callable[[float], None]) -> None:
    while (left := (when - clock()).total_seconds()) > 0:
        sleep(min(left, MAX_SLEEP.total_seconds()))


def _show(when: datetime) -> str:
    return when.astimezone(EASTERN).strftime("%a %Y-%m-%d %H:%M %Z")


# --- the real run --------------------------------------------------------------------------------


def nflverse_kickoffs(now: datetime) -> list[str]:
    """Kickoffs of this season's Games with no score yet, from nflverse (cached by nflreadpy)."""
    from ffp.players import load_schedule
    from ffp.predict import nfl_season

    return [game.kickoff for game in load_schedule(nfl_season(now)) if not game.scored]


def slate_and_push(output_dir: Path, url: str, slate_args: list[str], python: str = sys.executable,
                   runner: Callable = subprocess.run) -> Callable[[datetime], None]:
    """A `run_once`: `ffp slate --output-dir DIR`, then `ffp push DIR/expected-points.json`, each in its
    own process (a crash or leak in one run can't take the scheduler down)."""
    output = output_dir / SLATE_FILE

    def run(at: datetime) -> None:
        output.unlink(missing_ok=True)  # never push an earlier run's file
        _step(runner, "slate", [python, "-m", "ffp", "slate", "--output-dir", str(output_dir), *slate_args],
              SLATE_TIMEOUT)
        if not output.exists():
            raise RunFailed("slate", f"exited 0 but wrote no {output}")
        _step(runner, "push", [python, "-m", "ffp", "push", str(output), "--url", url], PUSH_TIMEOUT)

    return run


def _step(runner: Callable, step: str, command: list[str], timeout: timedelta) -> None:
    log.info("%s: %s", step, " ".join(command[1:]))
    try:
        done = runner(command, timeout=timeout.total_seconds())
    except subprocess.TimeoutExpired:
        raise RunFailed(step, f"timed out after {timeout}") from None
    if done.returncode != 0:
        raise RunFailed(step, f"exit status {done.returncode} (its output is above)")


# --- the command ---------------------------------------------------------------------------------


def add_parser(commands) -> None:
    """Register `ffp schedule` on the CLI's subcommands."""
    p = commands.add_parser("schedule", help="run ffp slate and ffp push on game days, until stopped")
    p.add_argument("--output-dir", type=Path, default=Path("."),
                   help=f"where ffp slate writes {SLATE_FILE} (default .)")
    p.add_argument("--url", default=push.DEFAULT_URL, help=f"where ffp push sends it (default {push.DEFAULT_URL})")
    p.add_argument("--slate-arg", action="append", default=[], metavar="ARG",
                   help="an extra argument for ffp slate, as --slate-arg=--jobs=4; repeat for several")
    p.add_argument("--once", action="store_true", help="run slate and push once now, then exit")
    p.add_argument("--show", type=int, metavar="N", help="print the next N run times and exit")
    p.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", force=True)
    if args.show:
        now = datetime.now(UTC)
        kickoffs = nflverse_kickoffs(now)
        for _ in range(args.show):
            now = next_run(now, kickoffs)
            print(_show(now))
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_once = slate_and_push(args.output_dir, args.url, args.slate_arg)
    if args.once:
        now = datetime.now(UTC)
        try:
            run_once(now)
        except RunFailed as failure:
            Alerts().run_failed(now, failure.step, failure.message)
            return 1
        Alerts().run_succeeded(now)
        return 0
    import time as _time

    log.info("scheduler started; pushing to %s", args.url)
    serve(load_kickoffs=nflverse_kickoffs, run_once=run_once, sleep=_time.sleep)
    return 0
