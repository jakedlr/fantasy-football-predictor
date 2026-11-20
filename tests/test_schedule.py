"""The game-day scheduler for `ffp slate` + `ffp push` (S11.3, ADR 0006)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ffp import schedule
from ffp.schedule import next_run

ET = ZoneInfo("America/New_York")


def et(text: str) -> datetime:
    """A US Eastern wall-clock time, e.g. "2026-11-22 13:00", as an aware UTC datetime."""
    return datetime.fromisoformat(text).replace(tzinfo=ET).astimezone(UTC)


# Week 12 of 2026 as the NFL plays it: TNF, a Sunday with early, late and SNF windows, then MNF.
WEEK = [et(t) for t in [
    "2026-11-19 20:15",                                                 # TNF
    *["2026-11-22 13:00"] * 6, "2026-11-22 16:05", "2026-11-22 16:05",  # Sunday early, late (two slots)
    "2026-11-22 16:25", "2026-11-22 16:25", "2026-11-22 20:20",         # ... and SNF
    "2026-11-23 20:15",                                                 # MNF
]]


def runs(start: str, kickoffs, n: int) -> list[str]:
    """The next n runs after `start` (US Eastern), as Eastern "MM-DD HH:MM"."""
    out, now = [], et(start)
    for _ in range(n):
        now = next_run(now, kickoffs)
        out.append(now.astimezone(ET).strftime("%m-%d %H:%M"))
    return out


def sunday(kickoffs=WEEK) -> list[str]:
    out, now = [], et("2026-11-22 00:00")
    while (now := next_run(now, kickoffs)).astimezone(ET).date().isoformat() == "2026-11-22":
        out.append(now.astimezone(ET).strftime("%H:%M"))
    return out


def test_a_sunday_runs_every_30_minutes_from_8_and_before_each_kickoff_window():
    # the site answers 429 to a push under 5 minutes after the last (S11.4): runs stay > 10 minutes apart
    expected = ["08:00", "08:30", "09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
                "12:50",  # 10 minutes before the early window; 13:00 is dropped, 10 minutes after it
                "13:30", "14:00", "14:30", "15:00", "15:30",
                "15:55",  # before the late window (16:05 and 16:25 kick off together); 16:00 dropped
                "16:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30",
                "20:10"]  # before SNF; 20:00 is dropped, 10 minutes before it
    assert sunday() == expected


def test_after_the_last_kickoff_of_the_day_it_sleeps_to_the_next_game_day():
    assert runs("2026-11-22 20:10", WEEK, 1) == ["11-23 08:00"]


def test_outside_game_days_it_waits_for_the_next_one():
    assert runs("2026-11-19 20:15", WEEK, 1) == ["11-22 08:00"]  # TNF kicked off: Friday and Saturday off
    assert runs("2026-11-17 12:00", WEEK, 1) == ["11-19 08:00"]


def test_monday_night():
    assert runs("2026-11-23 19:15", WEEK, 3)[:2] == ["11-23 19:30", "11-23 20:05"]


def test_a_monday_doubleheader_gets_a_run_before_each_game():
    mnf = [et("2026-11-23 19:15"), et("2026-11-23 20:15")]
    # 19:00 is dropped: 5 minutes before the 19:05 run, which would still be running
    assert runs("2026-11-23 18:31", mnf, 3) == ["11-23 19:05", "11-23 19:30", "11-23 20:05"]


def test_an_international_morning_game():
    london = [et("2026-11-08 09:30"), et("2026-11-08 13:00")]
    assert runs("2026-11-08 07:00", london, 5) == ["11-08 08:00", "11-08 08:30", "11-08 09:00", "11-08 09:20",
                                                   "11-08 10:00"]  # 09:30 dropped: 10 minutes after 09:20


def test_a_late_season_saturday():
    saturday = [et("2026-12-19 16:30"), et("2026-12-19 20:00"), et("2026-12-20 13:00")]
    assert runs("2026-12-19 15:31", saturday, 11) == [
        "12-19 16:00", "12-19 16:20", "12-19 17:00", "12-19 17:30", "12-19 18:00",
        "12-19 18:30", "12-19 19:00", "12-19 19:30", "12-19 19:50", "12-20 08:00", "12-20 08:30"]


def test_the_grid_follows_eastern_time_across_the_end_of_daylight_saving():
    # 2026-11-01: clocks go back at 02:00; the runs stay on Eastern :00 and :30.
    day = [et("2026-11-01 13:00")]
    first = next_run(et("2026-11-01 00:30"), day)
    assert first == datetime(2026, 11, 1, 13, 0, tzinfo=UTC)  # 08:00 EST is 13:00 UTC


def test_no_kickoffs_ahead_checks_again_in_a_day():
    now = et("2027-02-20 12:00")
    assert next_run(now, WEEK) == now + schedule.IDLE_RECHECK


def test_the_result_is_after_now_and_in_utc():
    now = et("2026-11-22 12:50")
    got = next_run(now, WEEK)
    assert got > now and got.tzinfo == UTC and got == et("2026-11-22 13:30")


def test_now_must_carry_a_timezone():
    with pytest.raises(ValueError):
        next_run(datetime(2026, 11, 22, 12), WEEK)


def test_kickoffs_may_be_iso_strings_as_nflverse_schedule_gives_them():
    as_text = [k.strftime("%Y-%m-%dT%H:%M:%SZ") for k in WEEK]
    assert sunday(as_text) == sunday()


# --- the loop --------------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start
        self.sleeps = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


class Recorder(schedule.Alerts):
    def __init__(self):
        self.events = []

    def run_succeeded(self, at):
        self.events.append(("ok", at))

    def run_failed(self, at, step, message):
        self.events.append(("failed", at, step, message))


def test_the_loop_runs_at_each_slot_and_reports_to_the_alert_hook():
    clock = FakeClock(et("2026-11-22 12:20"))
    ran = []

    def run_once(at):
        ran.append(clock().astimezone(ET).strftime("%H:%M"))
        clock.now += timedelta(minutes=4)  # a run takes a while
        if len(ran) == 2:
            raise schedule.RunFailed("slate", "exit status 1")

    alerts = Recorder()
    schedule.serve(load_kickoffs=lambda now: WEEK, run_once=run_once, clock=clock, sleep=clock.sleep,
                   alerts=alerts, max_runs=3)
    assert ran == ["12:30", "12:50", "13:30"]
    assert [e[0] for e in alerts.events] == ["ok", "failed", "ok"]
    assert alerts.events[1][2:] == ("slate", "exit status 1")


def test_a_failed_run_waits_for_the_next_slot_rather_than_retrying():
    clock = FakeClock(et("2026-11-22 12:29"))
    ran = []

    def run_once(at):
        ran.append(clock())
        raise schedule.RunFailed("push", "HTTP 503")

    schedule.serve(load_kickoffs=lambda now: WEEK, run_once=run_once, clock=clock, sleep=clock.sleep,
                   alerts=Recorder(), max_runs=2)
    assert ran[1] - ran[0] == timedelta(minutes=20)  # 12:30, then 12:50


def test_a_run_that_overruns_a_slot_skips_to_the_next_one():
    clock = FakeClock(et("2026-11-22 12:29"))
    ran = []

    def run_once(at):
        ran.append(clock().astimezone(ET).strftime("%H:%M"))
        clock.now += timedelta(minutes=35)

    schedule.serve(load_kickoffs=lambda now: WEEK, run_once=run_once, clock=clock, sleep=clock.sleep,
                   alerts=Recorder(), max_runs=2)
    assert ran == ["12:30", "13:30"]  # 12:50 passed while 12:30 ran


def test_the_loop_sleeps_in_short_steps_so_a_suspended_machine_wakes_on_time():
    clock = FakeClock(et("2026-11-20 12:00"))  # Friday: next run Sunday 08:00
    schedule.serve(load_kickoffs=lambda now: WEEK, run_once=lambda at: None, clock=clock, sleep=clock.sleep,
                   alerts=Recorder(), max_runs=1)
    assert max(clock.sleeps) <= schedule.MAX_SLEEP.total_seconds()


def test_a_schedule_that_fails_to_load_is_tried_again_in_an_hour():
    clock = FakeClock(et("2026-11-22 12:00"))
    calls = []

    def load(now):
        calls.append(now)
        if len(calls) == 1:
            raise OSError("nflverse down")
        return WEEK

    ran = []
    schedule.serve(load_kickoffs=load, run_once=lambda at: ran.append(clock()), clock=clock, sleep=clock.sleep,
                   alerts=Recorder(), max_runs=1)
    assert calls[1] - calls[0] == schedule.SCHEDULE_RETRY
    assert ran == [et("2026-11-22 13:30")]


# --- one real run: slate, then push ----------------------------------------------------------


class FakeRunner:
    """Stands in for subprocess.run: records commands; `slate` writes the file unless told not to."""

    def __init__(self, slate_status=0, push_status=0, writes=True, timeout_step=None):
        self.commands, self.timeouts = [], []
        self.slate_status, self.push_status, self.writes, self.timeout_step = slate_status, push_status, writes, timeout_step

    def __call__(self, command, timeout):
        import subprocess

        step = command[3]
        self.commands.append(command)
        self.timeouts.append(timeout)
        if step == self.timeout_step:
            raise subprocess.TimeoutExpired(command, timeout)
        if step == "slate" and self.writes:
            out = Path(command[command.index("--output-dir") + 1]) / schedule.SLATE_FILE
            out.write_text("{}")
        return subprocess.CompletedProcess(command, self.slate_status if step == "slate" else self.push_status)


def test_a_run_is_slate_then_push_of_its_file(tmp_path):
    runner = FakeRunner()
    schedule.slate_and_push(tmp_path, "http://127.0.0.1:1/api/expected-points", ["--jobs", "4"],
                            python="py", runner=runner)(et("2026-11-22 12:30"))
    assert runner.commands == [
        ["py", "-m", "ffp", "slate", "--output-dir", str(tmp_path), "--jobs", "4"],
        ["py", "-m", "ffp", "push", str(tmp_path / "expected-points.json"),
         "--url", "http://127.0.0.1:1/api/expected-points"],
    ]
    assert runner.timeouts == [schedule.SLATE_TIMEOUT.total_seconds(), schedule.PUSH_TIMEOUT.total_seconds()]


def test_a_failed_slate_pushes_nothing_not_even_the_last_file(tmp_path):
    (tmp_path / schedule.SLATE_FILE).write_text("{}")  # left by an earlier run
    runner = FakeRunner(slate_status=1, writes=False)
    with pytest.raises(schedule.RunFailed) as failed:
        schedule.slate_and_push(tmp_path, "u", [], runner=runner)(et("2026-11-22 12:30"))
    assert failed.value.step == "slate" and len(runner.commands) == 1
    assert not (tmp_path / schedule.SLATE_FILE).exists()


def test_a_slate_that_writes_nothing_fails(tmp_path):
    with pytest.raises(schedule.RunFailed, match="wrote no"):
        schedule.slate_and_push(tmp_path, "u", [], runner=FakeRunner(writes=False))(et("2026-11-22 12:30"))


@pytest.mark.parametrize("step", ["slate", "push"])
def test_a_step_that_hangs_is_stopped(tmp_path, step):
    with pytest.raises(schedule.RunFailed, match="timed out") as failed:
        schedule.slate_and_push(tmp_path, "u", [], runner=FakeRunner(timeout_step=step))(et("2026-11-22 12:30"))
    assert failed.value.step == step


def test_a_rejected_push_fails_the_run(tmp_path):
    with pytest.raises(schedule.RunFailed) as failed:
        schedule.slate_and_push(tmp_path, "u", [], runner=FakeRunner(push_status=1))(et("2026-11-22 12:30"))
    assert failed.value.step == "push"


def test_the_cli_knows_schedule():
    from ffp.cli import build_parser

    args = build_parser().parse_args(["schedule", "--output-dir", "/data/out", "--slate-arg=--jobs=4", "--once"])
    assert args.run is schedule.run and args.once and args.slate_arg == ["--jobs=4"]


def test_every_run_is_more_than_10_minutes_after_the_one_before():
    days = [WEEK, [et("2026-11-23 19:15"), et("2026-11-23 20:15")], [et("2026-11-08 09:30"), et("2026-11-08 13:00")],
            [et("2026-12-19 16:30"), et("2026-12-19 20:00")], [et("2026-11-26 12:30"), et("2026-11-26 16:30"),
                                                               et("2026-11-26 20:20")]]
    for kickoffs in days:
        now, times = et("2026-11-01 00:00"), []
        while (now := next_run(now, kickoffs)) < max(kickoffs):
            times.append(now)
        assert all(b - a > timedelta(minutes=10) for a, b in zip(times, times[1:]))


def test_a_run_that_ends_just_before_a_slot_waits_5_minutes_after_it_so_the_site_takes_the_push():
    clock = FakeClock(et("2026-11-22 12:29"))
    ran = []

    def run_once(at):
        ran.append(clock().astimezone(ET).strftime("%H:%M"))
        if len(ran) == 1:
            clock.now += timedelta(minutes=18, seconds=30)  # 12:30 run ends 12:48:30, before the 12:50 slot

    schedule.serve(load_kickoffs=lambda now: WEEK, run_once=run_once, clock=clock, sleep=clock.sleep,
                   alerts=Recorder(), max_runs=2)
    assert ran == ["12:30", "13:30"]  # 12:50 is under 5 minutes after the last push: skipped
