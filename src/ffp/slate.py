"""`ffp slate` (S11.2): the Expected Points push for the Leaderboard.

Every QB/RB/WR/TE with Prop Markets in the current NFL Week's Games that haven't kicked off, plus each
of those teams' K and D/ST, predicted by the same pipeline as `ffp predict` (one fit and simulation
per player), scored under every Leaderboard Scoring Rules option, keyed by Sleeper id, and written
as site/schema/expected-points.schema.json for `ffp push` to send (ADR 0006).

The NFL Week is ESPN's (PLAN, "Leaderboard site"): it starts on the first Wednesday, 3 AM Eastern,
after the previous week's last kickoff. Postseason weeks are numbered as ESPN and Sleeper number
them (Wild Card 1 ... Super Bowl 4); nflverse numbers them 19-22.
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ffp import predict as pipeline
from ffp.markets import format_utc, parse_utc
from ffp.players import (
    DST,
    TEAMS,
    Player,
    Roster,
    RosterEntry,
    ScheduledGame,
    UnknownTeamError,
    dst_player,
    load_roster,
    load_schedule,
    name_key,
    team_code_from_book_name,
)
from ffp.report import PlayerResult, SkippedPlayer
from ffp.scoring import load_rules
from ffp.sources import sleeper

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# The Leaderboard's Scoring Rules toggle (PLAN, "Leaderboard site"); the first is the run's own.
LEADERBOARD_RULES = ("ppr", "half-ppr", "standard", "espn-league")
DEFAULT_OUTPUT = Path("expected-points.json")
REGULAR_WEEKS = 18  # nflverse's postseason weeks follow on from 19 (since 2021)
SLEEPER_FIXTURE = "sleeper_players_nfl.json"  # a --fixtures directory's Sleeper player list

NO_KICKER = "no active or practice-squad kicker on the team's roster"
NO_SLEEPER_ID = "no Sleeper id (not in Sleeper's player list)"
NOT_ON_A_ROSTER = "Book name not a QB/RB/WR/TE on either team's roster"
TWO_ON_A_ROSTER = "Book name shared by two QB/RB/WR/TE on the Game's teams"

_EASTERN = ZoneInfo("America/New_York")
_WEEK_STARTS = (2, 3)  # Wednesday (weekday 2) at 3 AM Eastern, as ESPN's calendar starts its weeks


# --- the NFL Week -------------------------------------------------------------------


@dataclass(frozen=True)
class NflWeek:
    season: int
    week: int  # nflverse's number: postseason weeks are 19 on
    games: tuple[ScheduledGame, ...]

    @property
    def label(self) -> tuple[str, int]:
        """(seasonType, week) as ESPN and Sleeper number it: postseason weeks restart at 1."""
        if self.week <= REGULAR_WEEKS:
            return "regular", self.week
        return "postseason", self.week - REGULAR_WEEKS

    @property
    def number(self) -> int:
        return self.week

    @property
    def teams(self) -> set[str]:
        return {team for g in self.games for team in (g.home, g.away)}

    def upcoming(self, now: datetime) -> list[ScheduledGame]:
        """The week's Games that haven't kicked off at `now` (nor been scored)."""
        return [g for g in self.games if not g.scored and parse_utc(g.kickoff) > now]


def _week_start_after(moment: datetime) -> datetime:
    """The first Wednesday, 3 AM Eastern, after `moment`."""
    local = moment.astimezone(_EASTERN)
    weekday, hour = _WEEK_STARTS
    start = local.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=(weekday - local.weekday()) % 7)
    if start <= local:
        start += timedelta(days=7)
    # Re-attach the zone after the day arithmetic so a DST change in between keeps it at 3 AM local.
    return start.replace(tzinfo=None).replace(tzinfo=_EASTERN).astimezone(timezone.utc)


def current_week(schedule: Iterable[ScheduledGame], now: datetime) -> NflWeek:
    """The NFL Week under way at `now`: each week starts on the first Wednesday, 3 AM Eastern, after the
    previous week's last kickoff (the first week has no start). Before every week has started, the first
    week; after the last one, the last. `schedule` is one season's."""
    by_week: dict[int, list[ScheduledGame]] = {}
    for g in schedule:
        by_week.setdefault(g.week, []).append(g)
    if not by_week:
        raise ValueError("the schedule has no Games")
    weeks = sorted(by_week)
    chosen = weeks[0]
    for previous, week in zip(weeks, weeks[1:]):
        last = max(parse_utc(g.kickoff) for g in by_week[previous])
        if _week_start_after(last) <= now:
            chosen = week
    games = tuple(sorted(by_week[chosen], key=lambda g: g.kickoff))
    return NflWeek(games[0].season, chosen, games)


# --- the players --------------------------------------------------------------------


def slate_targets(games: Sequence[ScheduledGame], books: Sequence[pipeline.BookOdds],
                  roster: Sequence[RosterEntry]) -> tuple[list[pipeline.Target], list[SkippedPlayer]]:
    """Every QB/RB/WR/TE a Book prices in `games`, resolved to the roster by `name_key` and the Book's
    team, then each team's K and D/ST. A Book name with no one, or more than one, on the Game's teams'
    rosters is skipped and logged."""
    roster = roster if isinstance(roster, Roster) else Roster(roster)
    targets: dict[str, pipeline.Target] = {}
    unresolved: dict[str, str] = {}
    for book in books:
        to_game = {bg.game_id: g for bg in book.games for g in games if pipeline._same_game(bg, g)}
        for market in book.markets:
            game = to_game.get(market.game_id)
            if game is None or market.stat not in pipeline.MARKET_STATS:
                continue
            found = _resolve(market.player, market.team, game, roster)
            if isinstance(found, str):
                unresolved.setdefault(market.player, found)
            elif found.gsis_id not in targets:
                targets[found.gsis_id] = pipeline.Target(found.name, found, game)
    for name in [n for n in unresolved if any(t.name == n for t in targets.values())]:
        del unresolved[name]  # another Book's spelling of him resolved
    if unresolved:
        for why in sorted(set(unresolved.values())):
            names = sorted(name for name, reason in unresolved.items() if reason == why)
            log.warning("left out, %s: %s", why, ", ".join(names))
    skipped = [SkippedPlayer(name, why) for name, why in sorted(unresolved.items())]
    for game in games:
        for team in (game.away, game.home):
            kicker = _kicker(team, roster)
            if kicker is None:
                skipped.append(SkippedPlayer(f"{team} K", NO_KICKER))
            else:
                targets.setdefault(kicker.gsis_id, pipeline.Target(kicker.name, kicker, game))
            dst = dst_player(team)
            targets[dst.gsis_id] = pipeline.Target(dst.name, dst, game)
    return list(targets.values()), skipped


def _resolve(name: str, book_team: str | None, game: ScheduledGame, roster: Roster) -> Player | str:
    """The QB/RB/WR/TE on the Game's teams a Book's name means, or why there is none."""
    teams = {game.home, game.away}
    same = [e for e in roster.named(name_key(name)) if e.position in pipeline.PLAYER_POSITIONS and e.team in teams]
    try:
        team = team_code_from_book_name(book_team) if book_team else None
    except UnknownTeamError:
        team = None
    if team is not None and len(same) > 1:
        same = [e for e in same if e.team == team] or same
    if not same:
        return NOT_ON_A_ROSTER
    if len(same) > 1:
        return TWO_ON_A_ROSTER
    e = same[0]
    return Player(e.gsis_id, e.name, e.position, e.team)


def _kicker(team: str, roster: Roster) -> Player | None:
    """The team's kicker: its active K on the roster, else its practice-squad one (DEV: midweek, a team
    that cut its kicker may have only one to elevate); the latest week's, then by name, if several."""
    on_team = [e for e in roster if e.team == team and e.position == "K"]
    kickers = [e for e in on_team if e.status == "ACT"] or [e for e in on_team if e.status == "DEV"]
    if not kickers:
        return None
    kickers.sort(key=lambda e: (-e.week, e.name))
    if len(kickers) > 1 or kickers[0].status != "ACT":
        log.info("%s's kicker: %s (%s), of %s", team, kickers[0].name, kickers[0].status,
                 ", ".join(e.name for e in kickers))
    e = kickers[0]
    return Player(e.gsis_id, e.name, e.position, e.team)


# --- the push -----------------------------------------------------------------------


def stat_means(means: Mapping[str, float]) -> dict[str, float]:
    """Stat means as the push carries them: rounded, and never below 0 (the schema's minimum)."""
    return {stat: max(0.0, round(float(v), 4)) for stat, v in means.items()}


def _dst_name(team: str) -> str:
    return f"{TEAMS[team][1]} D/ST"


def push_players(results: Sequence[PlayerResult], targets: Sequence[pipeline.Target],
                 roster: Sequence[RosterEntry], ids: sleeper.SleeperIds) -> tuple[list[dict], list[SkippedPlayer]]:
    """Each result as a push player, keyed by Sleeper id; those without one are left out (and returned)."""
    by_id = {t.player.gsis_id: t for t in targets}
    entries = {e.gsis_id: e for e in roster}
    players, missing = [], []
    for r in results:
        target = by_id[r.player_id]
        player = target.player
        if player.position == DST:
            sleeper_id, espn_id, gsis_id, name = sleeper.dst_id(player), None, None, _dst_name(player.team)
        else:
            sleeper_id, espn_id = ids.lookup(entries[player.gsis_id])
            gsis_id, name = player.gsis_id, player.name
        if sleeper_id is None:
            missing.append(SkippedPlayer(player.name, NO_SLEEPER_ID))
            continue
        players.append({
            "sleeperId": sleeper_id, "gsisId": gsis_id, "espnId": espn_id, "name": name,
            "team": sleeper.team_code(player.team), "position": "D/ST" if player.position == DST else player.position,
            "kickoff": format_utc(parse_utc(target.game.kickoff)),
            "statMeans": stat_means(r.stat_means),
            "expected": {rules: round(r.expected[rules], 4) for rules in LEADERBOARD_RULES},
        })
    if missing:
        log.warning("%d players left out with no Sleeper id: %s", len(missing), ", ".join(s.name for s in missing))
    return players, missing


def build_push(week: NflWeek, players: list[dict], id_map: Mapping[str, str], generated_at: datetime) -> dict:
    season_type, number = week.label
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": format_utc(generated_at),
        "season": week.season,
        "seasonType": season_type,
        "week": number,
        "players": players,
        "idMap": dict(sorted(id_map.items(), key=lambda kv: int(kv[0]))),
    }


def write_push(push: dict, path: Path) -> int:
    """Write the push atomically (a reader never sees half of it); returns its size in bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(push, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(data)


# --- the command --------------------------------------------------------------------


def add_parser(commands) -> None:
    """`ffp slate`'s options, on the CLI's subcommands."""
    from ffp.cli import _jobs

    p = commands.add_parser("slate", help="Expected Points for every priced player this NFL Week (the Leaderboard push)")
    p.add_argument("--fixtures", type=Path, help="replay recorded sportsbook responses, nflverse CSVs and Sleeper's "
                                                 f"player list ({SLEEPER_FIXTURE}) from this directory")
    p.add_argument("--now", type=parse_utc, help="as if it were this time (ISO 8601, UTC unless an offset is given); "
                                                 "default: now")
    p.add_argument("--jobs", type=_jobs, metavar="N", help="worker processes for predicting players (default: one "
                                                           "per player, leaving a fifth of the cores free)")
    p.add_argument("--output-dir", type=Path, default=Path("."),
                   help=f"where {DEFAULT_OUTPUT} goes (default .)")
    p.add_argument("--output", type=Path, metavar="FILE",
                   help=f"write the push JSON to FILE instead of OUTPUT_DIR/{DEFAULT_OUTPUT}")
    p.add_argument("--snapshots", type=Path, default=Path("snapshots"),
                   help="where live runs save their raw responses and Fair Probabilities (default snapshots/)")
    p.add_argument("--no-snapshot", action="store_true", help="don't save a snapshot of a live run")


def _load_books(args, games: Sequence[ScheduledGame], now: datetime):
    """(the Books' odds for `games`, the run's Snapshot or None); prints the odds timing line."""
    from ffp import cli

    if args.fixtures:
        return pipeline.load_fixture_books(args.fixtures), None
    from ffp.sources.http import Http

    snapshot = None if args.no_snapshot else cli._open_snapshot(args.snapshots, datetime.now(timezone.utc))
    http, started = Http(), time.monotonic()
    books = pipeline.load_live_books(
        http, games, now, on_response=cli._response_saver(snapshot),
        stats=pipeline.market_stats_for(pipeline.MODELED_POSITIONS),
        kickers=True, dst_games=games, kicker_ladders=snapshot is not None,
    )
    seconds = round(time.monotonic() - started, 2)
    if http.timing:
        print(cli.timing_line(http.timing, seconds))
        if snapshot is not None:
            snapshot.save_timing(http.timing, seconds)
    return books, snapshot


def _sleeper_ids(args) -> sleeper.SleeperIds:
    if args.fixtures:
        payload = json.loads((Path(args.fixtures) / SLEEPER_FIXTURE).read_text())
        return sleeper.SleeperIds(sleeper.parse_players(payload))
    from ffp.sources.http import Http

    return sleeper.SleeperIds(sleeper.load_players(Http().get_json, today=datetime.now(timezone.utc).date()))


def run(args: argparse.Namespace) -> int:
    """`ffp slate`: write the push JSON. 0 when written; 1 when neither Book answered or Sleeper's list
    couldn't be had (nothing is written, so an older file isn't replaced by an emptier one)."""
    from ffp import cli
    from ffp.sources.http import FetchError

    started = time.monotonic()
    now = args.now or datetime.now(timezone.utc)
    season = pipeline.nfl_season(now)
    if args.fixtures:
        roster, schedule = pipeline.load_fixture_nflverse(args.fixtures, season)
    else:
        roster, schedule = load_roster(season), load_schedule(season)
    week = current_week(schedule, now)
    games = week.upcoming(now)
    roster = Roster(roster)

    history = None
    if not args.fixtures and games:
        history = cli._Background(lambda: cli._player_history(season))
    snapshot = None
    if games:
        books, snapshot = _load_books(args, games, now)
        if not books:
            if history is not None:
                history.wait()
            print("error: could not get odds from DraftKings or FanDuel; see the warnings above", file=sys.stderr)
            return 1
        targets, skipped = slate_targets(games, books, roster)
    else:
        books, targets, skipped = [], [], []
    try:
        ids = _sleeper_ids(args)
    except (FetchError, OSError, ValueError) as error:
        if history is not None:
            history.wait()
        print(f"error: Sleeper's player list is unavailable ({error})", file=sys.stderr)
        return 1
    td_history, recent_games = history.result() if history is not None else ({}, {})

    rules = [load_rules(name) for name in LEADERBOARD_RULES]
    results, unfitted = pipeline.predict(
        targets, books, rules[0], on_fair_points=cli._fair_points_saver(snapshot), td_history=td_history,
        recent_games=recent_games, jobs=args.jobs, also_score=rules[1:],
    )
    players, missing = push_players(results, targets, roster, ids)
    id_map = ids.id_map(week.teams, roster)
    # The prices' time: the real clock, or --now when replaying (/api/expected-points refuses a
    # generatedAt more than 5 minutes ahead, and freezes players whose kickoff is at or before it).
    push = build_push(week, players, id_map, args.now or datetime.now(timezone.utc))
    output = args.output or args.output_dir / DEFAULT_OUTPUT
    try:
        size = write_push(push, output)
    except OSError as error:
        print(f"error: could not write {output}: {error}", file=sys.stderr)
        return 1

    skipped = [*skipped, *unfitted, *missing]
    season_type, number = week.label
    which = f"week {number}" if season_type == "regular" else f"postseason week {number}"
    print(f"Wrote {output}: {len(players)} players in {len(games)} of {which}'s {len(week.games)} Games, "
          f"{len(id_map)} ids in the id map, {size:,} bytes, in {time.monotonic() - started:.1f} s"
          + (f"; snapshot in {snapshot.run_dir}" if snapshot else ""))
    if skipped:
        reasons = Counter(_reason(s.reason) for s in skipped)
        print(f"{len(skipped)} skipped: " + "; ".join(f"{n} {why}" for why, n in reasons.most_common()))
    return 0


def _reason(text: str) -> str:
    """A skip reason without its details (a fit error's message), so the summary groups them."""
    return text.split(" (", 1)[0] if text.startswith("could not fit") else text
