"""INPUT.md names -> Score Distributions.

Players and their next Games (nflverse) -> both books' Prop Markets for those
Games -> Fair Probabilities -> Stat Distributions -> simulation.
"""

import csv
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ffp.distributions import FitError, StatDistribution, fit_receiving_yards
from ffp.fair import FairPoint, fair_points
from ffp.markets import Game, PropMarket
from ffp.players import (
    PROPS_NOT_POSTED,
    Player,
    PlayerError,
    Roster,
    RosterEntry,
    ScheduledGame,
    UnknownTeamError,
    game_in_progress,
    load_roster,
    load_schedule,
    name_key,
    next_game,
    resolve_player,
    team_code_from_book_name,
)
from ffp.report import PlayerResult, SkippedPlayer, StatDetail, chances
from ffp.scoring import ScoringRules
from ffp.simulate import simulate_scores
from ffp.sources import draftkings, fanduel

log = logging.getLogger(__name__)

STAT = "receiving_yards"
# Receiving yards has a model for these positions; RB and QB arrive in Sprint 4.
MODELED_POSITIONS = ("WR", "TE")
# Sprint 2 simplification, reported on every player so the output can't be mistaken for the finished model.
FLAGS = ("receiving yards only (other Scoring Stats arrive in Sprint 3)",)

NO_MARKETS = "no Prop Markets found for this player"
NO_GAME = "no remaining Game on the schedule"
# A book's Game is the scheduled Game when it has the same two teams and kicks off within this of it.
# Books' kickoffs differ from nflverse's by minutes (FD lists MNF at 01:21Z, nflverse 01:15Z).
KICKOFF_TOLERANCE = timedelta(hours=12)


@dataclass(frozen=True)
class BookOdds:
    """One book's Games and receiving-yards Prop Markets."""

    book: str
    games: list[Game]
    markets: list[PropMarket]


@dataclass(frozen=True)
class Target:
    """One INPUT.md line resolved to a Player and the Game to predict."""

    name: str  # as written in INPUT.md
    player: Player
    game: ScheduledGame


# --- INPUT.md --------------------------------------------------------------------

# A list marker ("- ", "* ", "1. ") with an optional task checkbox ("[ ] ", "[x] "), or a bare marker.
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])(?:\s+|$)(?:\[[ xX]\](?:\s+|$))?")
# A thematic break or front-matter fence: "---", "***", "___", "- - -", ...
_RULE = re.compile(r"^(?:([-*_])\s*){3,}$")
_EMPHASIS = re.compile(r"[*_`]")


def read_input(path: Path) -> list[str]:
    """Player names from INPUT.md, one per line.

    Blank lines, Markdown headings, horizontal rules and YAML front matter are
    ignored; list markers, task checkboxes and emphasis (**, _, `) are stripped,
    so a plain list and a Markdown list both work.
    """
    lines = [line.strip() for line in Path(path).read_text().splitlines()]
    if lines and lines[0] == "---" and "---" in lines[1:]:  # YAML front matter
        lines = lines[lines.index("---", 1) + 1 :]
    names = []
    for line in lines:
        if not line or line.startswith("#") or _RULE.match(line):
            continue
        names.append(_EMPHASIS.sub("", _LIST_MARKER.sub("", line)).strip())
    return [name for name in names if name]


# --- Players and Games -------------------------------------------------------------


def nfl_season(now: datetime) -> int:
    """The NFL season under way at `now`: a season's games run September to February."""
    return now.year if now.month >= 3 else now.year - 1


def resolve_targets(
    names: Sequence[str], roster: Sequence[RosterEntry], schedule: Sequence[ScheduledGame], now: datetime
) -> tuple[list[Target], list[SkippedPlayer]]:
    """Each name's Player and next Game, or the reason it can't be predicted."""
    roster = roster if isinstance(roster, Roster) else Roster(roster)
    targets, skipped = [], []
    for name in names:
        try:
            player = resolve_player(name, roster)
        except PlayerError as error:
            skipped.append(SkippedPlayer(name, str(error)))
            continue
        if player.position not in MODELED_POSITIONS:
            skipped.append(SkippedPlayer(name, _unmodeled(player)))
            continue
        playing = game_in_progress(player.team, schedule, now)
        if playing is not None:
            skipped.append(SkippedPlayer(name, _in_progress(playing)))
            continue
        game = next_game(player.team, schedule, now)
        if game is None:
            skipped.append(SkippedPlayer(name, NO_GAME))
            continue
        targets.append(Target(name, player, game))
    return targets, skipped


def _in_progress(game: ScheduledGame) -> str:
    return (
        f"this week's Game ({game.away} @ {game.home}) has already kicked off; "
        "rerun after it ends to predict the next one"
    )


def _unmodeled(player: Player) -> str:
    return (
        f"{player.position} is not supported yet: receiving yards are modelled for "
        f"{'/'.join(MODELED_POSITIONS)} only (RB and QB arrive in Sprint 4)"
    )


# --- Book odds ---------------------------------------------------------------------


def load_fixture_books(fixtures: Path) -> list[BookOdds]:
    """DraftKings and FanDuel receiving-yards Prop Markets and Games from recorded responses."""
    fixtures = Path(fixtures)
    ladder = _read_json(fixtures / "dk_nfl_rec_yds_ladder.json")
    main_line = _read_json(fixtures / "dk_nfl_rec_yds_ou.json")
    fd_tab = _read_json(fixtures / "fd_nfl_event_receiving_tab.json")
    return [
        BookOdds(
            draftkings.BOOK,
            draftkings.parse_games(ladder),
            draftkings.parse_prop_markets(ladder, main_line, STAT),
        ),
        BookOdds(fanduel.BOOK, fanduel.parse_games(fd_tab), fanduel.parse_prop_markets(fd_tab, STAT)),
    ]


def load_fixture_nflverse(
    fixtures: Path, season: int
) -> tuple[list[RosterEntry], list[ScheduledGame]]:
    """Roster and schedule from `nflverse_rosters_weekly_<season>.csv` and `nflverse_schedule_<season>.csv`."""
    fixtures = Path(fixtures)
    roster = load_roster(season, fetch=lambda s: _read_csv(fixtures / f"nflverse_rosters_weekly_{s}.csv"))
    schedule = load_schedule(season, fetch=lambda s: _read_csv(fixtures / f"nflverse_schedule_{s}.csv"))
    return roster, schedule


def load_live_books(
    http,
    games: Sequence[ScheduledGame],
    now: datetime,
    on_response: Callable | None = None,
) -> list[BookOdds]:
    """Both books' receiving-yards Prop Markets for `games`, fetched live.

    DraftKings serves every Game in one pair of subcategory responses. FanDuel
    needs one request per Game, so only the events matching `games` are
    fetched. A book that fails is logged and left out; the other still counts.
    """
    from ffp.sources import live

    failures = _book_failures()
    books = []
    try:
        dk = live.fetch_draftkings(http, [STAT], on_response=on_response)
        books.append(
            BookOdds(
                draftkings.BOOK,
                draftkings.parse_games(dk["dk_league"]),
                draftkings.parse_prop_markets(dk["dk_rec_yds_ladder"], dk["dk_rec_yds_ou"], STAT),
            )
        )
    except failures as error:
        log.warning("DraftKings left out: %s", _describe(error))

    try:
        fd_games = fanduel.parse_games(live.fetch_fanduel_league(http, on_response=on_response), now)
    except failures as error:
        log.warning("FanDuel left out: %s", _describe(error))
        return books
    wanted = sorted({g.game_id for g in fd_games for s in games if _same_game(g, s)})
    markets = []
    for event_id in wanted:  # one event at a time, so one failed event costs only its own Game
        try:
            tabs = live.fetch_fanduel_events(http, [event_id], tabs=[fanduel.TABS[STAT]], on_response=on_response)
            markets += [m for tab in tabs.values() for m in fanduel.parse_prop_markets(tab, STAT)]
        except failures as error:
            log.warning("FanDuel event %s left out: %s", event_id, _describe(error))
    books.append(BookOdds(fanduel.BOOK, fd_games, markets))
    return books


# What a book can fail with, short of a bug in our own code: the request failing, the
# catalogue lacking a market, or a response whose shape changed under the parser.
def _book_failures() -> tuple[type[Exception], ...]:
    from ffp.sources.http import FetchError
    from ffp.sources.live import SubcategoryNotFound

    return (FetchError, SubcategoryNotFound, KeyError, IndexError, TypeError, ValueError, AttributeError)


def _describe(error: Exception) -> str:
    """An exception as one line: a KeyError's str() is only the missing key."""
    return f"{type(error).__name__}: {error}"


def _same_game(book_game: Game, game: ScheduledGame) -> bool:
    """Whether a book's Game is the scheduled Game: same two teams, kickoff close, not started.

    Books and nflverse share neither game ids nor team spellings, so teams go
    through `team_code_from_book_name`.
    """
    if book_game.started:
        return False
    try:
        teams = {team_code_from_book_name(book_game.home), team_code_from_book_name(book_game.away)}
    except UnknownTeamError:  # e.g. an all-star or futures "game"
        return False
    if teams != {game.home, game.away}:
        return False
    return abs(_parse_utc(book_game.kickoff) - _parse_utc(game.kickoff)) <= KICKOFF_TOLERANCE


def _parse_utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


# --- Fit and simulate --------------------------------------------------------------


def predict(
    targets: Sequence[Target],
    books: Sequence[BookOdds],
    rules: ScoringRules,
    on_fair_points: Callable[[Target, list[FairPoint]], None] | None = None,
) -> tuple[list[PlayerResult], list[SkippedPlayer]]:
    """A Score Distribution summary for each target with Prop Markets in its Game.

    Every book's markets for the player go into one fit (S2.5). With no book
    pricing the Game at all, the player is skipped as "props not posted"; with
    the Game priced but not the player, as "no Prop Markets".
    """
    results, skipped = [], []
    for target in targets:
        priced, markets = _markets_for(target, books)
        if not markets:
            skipped.append(SkippedPlayer(target.name, NO_MARKETS if priced else PROPS_NOT_POSTED))
            continue
        points = fair_points(markets)
        if on_fair_points is not None:
            on_fair_points(target, points)
        # Only a fit failure skips the player; any other error is a bug or bad configuration and stops the run.
        try:
            receiving_yards = fit_receiving_yards(points, target.player.position)
        except FitError as error:
            skipped.append(SkippedPlayer(target.name, f"could not fit receiving yards: {error}"))
            continue
        results.append(_player_result(target, markets, receiving_yards, rules))
    return results, skipped


def _markets_for(target: Target, books: Sequence[BookOdds]) -> tuple[bool, list[PropMarket]]:
    """(whether any book prices the target's Game, the player's markets in it across books)."""
    key = name_key(target.player.name)
    priced, markets = False, []
    for book in books:
        game_ids = {g.game_id for g in book.games if _same_game(g, target.game)}
        in_game = [m for m in book.markets if m.game_id in game_ids and m.stat == STAT]
        priced = priced or bool(in_game)
        markets += [m for m in in_game if name_key(m.player) == key]
    return priced, markets


def _player_result(
    target: Target, markets: list[PropMarket], receiving_yards: StatDistribution, rules: ScoringRules
) -> PlayerResult:
    scores = simulate_scores({STAT: receiving_yards}, rules)
    game = target.game
    return PlayerResult(
        player=target.name,
        team=target.player.team,
        game=f"{game.away} @ {game.home}",
        kickoff=game.kickoff,
        position=target.player.position,
        percentiles=scores.percentiles(),
        chances=chances(scores),
        flags=list(FLAGS),
        stats={
            STAT: StatDetail(
                mean=float(receiving_yards.mean),
                p50=float(receiving_yards.ppf(0.5)),
                books=sorted({m.book for m in markets}),
                rungs=sum(len(m.ladder) for m in markets),
            )
        },
    )


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))
