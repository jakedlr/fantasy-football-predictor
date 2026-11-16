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

import numpy as np

from ffp import params
from ffp.base_rates import base_rate_stats
from ffp.distributions import (
    FitError,
    StatDistribution,
    fit_receiving_yards,
    fit_receptions,
    fit_touchdowns,
    split_touchdowns,
)
from ffp.fair import FairPoint, fair_points
from ffp.markets import Game, PropMarket, parse_utc
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

TD_STAT = fanduel.TD_STAT  # rushing + receiving TDs as the books price them; split after the fit
# The stats fitted from Prop Markets, in fitting order: receptions first, since they set
# receiving yards' P(0). Their names in "incomplete: missing ..." flags.
MARKET_STATS = {"receptions": "receptions", "receiving_yards": "receiving yards", TD_STAT: "touchdowns"}
# WR/TE are modelled in full; RB and QB arrive in Sprint 4.
MODELED_POSITIONS = ("WR", "TE")
# What the model leaves out for every WR/TE, listed once in the report's assumptions.
MODEL_ASSUMPTIONS = (
    "WR/TE rushing yards are not modelled yet (no rushing markets are fetched); they count as 0 points.",
)

NO_MARKETS = "no Prop Markets found for this player"
NO_GAME = "no remaining Game on the schedule"
# A book's Game is the scheduled Game when it has the same two teams and kicks off within this of it.
# Books' kickoffs differ from nflverse's by minutes (FD lists MNF at 01:21Z, nflverse 01:15Z).
KICKOFF_TOLERANCE = timedelta(hours=12)


@dataclass(frozen=True)
class BookOdds:
    """One book's Games and its Prop Markets for MARKET_STATS."""

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
    """DraftKings and FanDuel Prop Markets for MARKET_STATS and their Games, from recorded responses."""
    fixtures = Path(fixtures)

    def read(name: str) -> dict:
        return _read_json(fixtures / name)

    ladder = read("dk_nfl_rec_yds_ladder.json")
    dk_markets = (
        draftkings.parse_prop_markets(ladder, read("dk_nfl_rec_yds_ou.json"), "receiving_yards")
        + draftkings.parse_prop_markets(
            read("dk_nfl_receptions_ladder.json"), read("dk_nfl_receptions_ou.json"), "receptions"
        )
        + draftkings.parse_prop_markets(read("dk_nfl_td_scorer.json"), None, TD_STAT)
        + draftkings.parse_prop_markets(read("dk_nfl_3plus_tds.json"), None, TD_STAT)
    )
    receiving_tab = read("fd_nfl_event_receiving_tab.json")
    td_tab = read("fd_nfl_event_td_scorer_tab.json")
    fd_markets = (
        fanduel.parse_prop_markets(receiving_tab, "receiving_yards")
        + fanduel.parse_prop_markets(receiving_tab, "receptions")
        + fanduel.parse_prop_markets(td_tab, TD_STAT)
    )
    return [
        BookOdds(draftkings.BOOK, draftkings.parse_games(ladder), dk_markets),
        BookOdds(fanduel.BOOK, fanduel.parse_games(receiving_tab), fd_markets),
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
    """Both books' Prop Markets for MARKET_STATS in `games`, fetched live.

    DraftKings serves every Game in one set of subcategory responses per stat;
    each stat is fetched on its own, so a stat it doesn't list yet costs only
    that stat. FanDuel needs one request per Game and tab, so only the events
    matching `games` are fetched, one at a time. A book, stat or event that
    fails is logged and left out; the rest still count.
    """
    from ffp.sources import live

    failures = _book_failures()
    on_response = _once(on_response)
    books = []

    dk_markets, dk_games = [], None
    for stat in MARKET_STATS:
        try:
            dk = live.fetch_draftkings(http, [stat], on_response=on_response)
            slug = live.DK_STATS[stat].slug
            dk_markets += draftkings.parse_prop_markets(dk[f"dk_{slug}_ladder"], dk.get(f"dk_{slug}_ou"), stat)
            dk_games = dk_games or draftkings.parse_games(dk["dk_league"])
        except failures as error:
            log.warning("DraftKings %s left out: %s", MARKET_STATS[stat], _describe(error))
    if dk_games is not None:
        books.append(BookOdds(draftkings.BOOK, dk_games, dk_markets))

    try:
        fd_games = fanduel.parse_games(live.fetch_fanduel_league(http, on_response=on_response), now)
    except failures as error:
        log.warning("FanDuel left out: %s", _describe(error))
        return books
    wanted = sorted({g.game_id for g in fd_games for s in games if _same_game(g, s)})
    tabs = live.fd_tabs(MARKET_STATS)
    fd_markets = []
    for event_id in wanted:  # one event at a time, so one failed event costs only its own Game
        for tab in tabs:
            try:
                [payload] = live.fetch_fanduel_events(http, [event_id], tabs=[tab], on_response=on_response).values()
                for stat in MARKET_STATS:
                    if fanduel.TABS[stat] == tab:
                        fd_markets += fanduel.parse_prop_markets(payload, stat)
            except failures as error:
                log.warning("FanDuel event %s tab %s left out: %s", event_id, tab, _describe(error))
    books.append(BookOdds(fanduel.BOOK, fd_games, fd_markets))
    return books


def _once(on_response: Callable | None) -> Callable | None:
    """on_response, called once per (book, name, url): the cached league response is reused per stat."""
    if on_response is None:
        return None
    seen = set()

    def call(book: str, name: str, url: str, payload) -> None:
        if (book, name, url) not in seen:
            seen.add((book, name, url))
            on_response(book, name, url, payload)

    return call


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
    return abs(parse_utc(book_game.kickoff) - parse_utc(game.kickoff)) <= KICKOFF_TOLERANCE


# --- Fit and simulate --------------------------------------------------------------


def predict(
    targets: Sequence[Target],
    books: Sequence[BookOdds],
    rules: ScoringRules,
    on_fair_points: Callable[[Target, str, list[FairPoint]], None] | None = None,
    correlation: str | None = None,
) -> tuple[list[PlayerResult], list[SkippedPlayer]]:
    """A Score Distribution for each target with Prop Markets in its Game.

    Each market stat is fitted from every book's markets for the player (S2.5).
    Receptions set receiving yards' P(0); the anytime-TD rate is split into
    rushing and receiving TDs by the position's share; fumbles lost and 2-pt
    come from Base Rates. The stats are sampled together through the
    position's latent correlation matrix, `correlation` "within" (default) or
    "pooled" (S3.6). A stat without a market, or whose fit fails, is left out
    and flagged "incomplete" (S3.5). With no book pricing the Game at all,
    the player is skipped as "props not posted"; with the Game priced but not
    the player, as "no Prop Markets"; with no fit succeeding, as unfittable.
    """
    results, skipped = [], []
    for target in targets:
        priced, by_stat = _markets_for(target, books)
        if not any(by_stat.values()):
            skipped.append(SkippedPlayer(target.name, NO_MARKETS if priced else PROPS_NOT_POSTED))
            continue
        fitted = _fit_markets(target, by_stat, on_fair_points)
        if not fitted.dists:
            reason = f"could not fit any of its markets ({'; '.join(fitted.errors)})"
            skipped.append(SkippedPlayer(target.name, reason))
            continue
        results.append(_player_result(target, fitted, rules, correlation))
    return results, skipped


@dataclass
class _Fitted:
    """One player's fitted market stats: Stat Distributions by Scoring Stat, their report
    details, the markets behind each, and the "incomplete" flags and fit errors."""

    dists: dict[str, StatDistribution]
    details: dict[str, StatDetail]
    flags: list[str]
    errors: list[str]


def _fit_markets(target: Target, by_stat: dict[str, list[PropMarket]], on_fair_points) -> _Fitted:
    position = target.player.position
    fitted = _Fitted({}, {}, [], [])

    def fit(stat: str, fitter):
        markets = by_stat.get(stat)
        label = MARKET_STATS[stat]
        if not markets:
            fitted.flags.append(f"incomplete: missing {label}")
            return None, []
        points = fair_points(markets)
        if on_fair_points is not None:
            on_fair_points(target, stat, points)
        try:
            return fitter(points), markets
        except FitError as error:  # a bug or bad configuration raises something else and stops the run
            fitted.flags.append(f"incomplete: could not fit {label} ({error})")
            fitted.errors.append(f"{label}: {error}")
            return None, []

    receptions, markets = fit("receptions", lambda pts: fit_receptions(pts, position))
    if receptions is not None:
        fitted.dists["receptions"] = receptions
        fitted.details["receptions"] = _detail(receptions, markets)
    # No catch, no yards (research Q4): with a receptions fit, P(0 yards) is P(0 receptions).
    pi0 = None if receptions is None else float(1 - receptions.sf(np.array([1]))[0])
    yards, markets = fit("receiving_yards", lambda pts: fit_receiving_yards(pts, position, pi0=pi0))
    if yards is not None:
        fitted.dists["receiving_yards"] = yards
        fitted.details["receiving_yards"] = _detail(yards, markets)
    tds, markets = fit(TD_STAT, fit_touchdowns)
    if tds is not None:
        share = params.load("base_rates")["td_rushing_share"][position]
        rushing, receiving = split_touchdowns(tds, share)
        for stat, dist in (("receiving_tds", receiving), ("rushing_tds", rushing)):
            fitted.dists[stat] = dist
            fitted.details[stat] = _detail(dist, markets)
    return fitted


def _detail(dist: StatDistribution, markets: list[PropMarket]) -> StatDetail:
    return StatDetail(
        mean=float(dist.mean),
        p50=float(np.asarray(dist.ppf(np.array([0.5])))[0]),
        books=sorted({m.book for m in markets}),
        rungs=sum(len(m.ladder) for m in markets),
    )


def _markets_for(target: Target, books: Sequence[BookOdds]) -> tuple[bool, dict[str, list[PropMarket]]]:
    """(whether any book prices the target's Game, the player's markets in it across books, by stat)."""
    key = name_key(target.player.name)
    priced, by_stat = False, {stat: [] for stat in MARKET_STATS}
    for book in books:
        game_ids = {g.game_id for g in book.games if _same_game(g, target.game)}
        in_game = [m for m in book.markets if m.game_id in game_ids and m.stat in by_stat]
        priced = priced or bool(in_game)
        for market in in_game:
            if name_key(market.player) == key:
                by_stat[market.stat].append(market)
    return priced, by_stat


def _player_result(target: Target, fitted: _Fitted, rules: ScoringRules, correlation: str | None) -> PlayerResult:
    position = target.player.position
    receptions = fitted.dists.get("receptions")
    stat_dists = dict(fitted.dists)
    stat_dists.update(base_rate_stats(position, receptions_mean=None if receptions is None else receptions.mean))
    scores = simulate_scores(stat_dists, rules, corr=params.correlation(position, correlation))
    game = target.game
    return PlayerResult(
        player=target.name,
        team=target.player.team,
        game=f"{game.away} @ {game.home}",
        kickoff=game.kickoff,
        position=position,
        percentiles=scores.percentiles(),
        chances=chances(scores),
        flags=fitted.flags,
        stats=fitted.details,
    )


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))
