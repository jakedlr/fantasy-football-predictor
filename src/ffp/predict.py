"""INPUT.md names -> Score Distributions.

Players and their next Games (nflverse) -> both books' Prop Markets for those
Games -> Fair Probabilities -> Stat Distributions -> simulation.
"""

import csv
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from ffp import params
from ffp.base_rates import base_rate_stats
from ffp.distributions import (
    FitError,
    StatDistribution,
    fit_carries,
    fit_interceptions,
    fit_passing_tds,
    fit_passing_yards,
    fit_receiving_yards,
    fit_receptions,
    fit_rushing_yards,
    fit_touchdowns,
    prob_zero,
    split_touchdowns,
)
from ffp.estimates import RUSHING_TDS, RecentGames, estimate_mean, estimated_distribution
from ffp.fair import DEFAULT_PRIOR_C, TD_PRIOR_C, FairPoint, fair_points
from ffp.markets import DST_TDS, TEAM_POINTS, Game, GameLine, PropMarket, parse_utc
from ffp.players import (
    DST,
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
from ffp.simulate import DEFAULT_SAMPLES, DEFAULT_SEED, ScoreDistribution, simulate_scores
from ffp.sources import draftkings, fanduel
from ffp.td_share import TdHistory, rushing_td_share
from ffp import gamelines
from ffp.dst import dst_scores
from ffp.kicker import kicker_scores

log = logging.getLogger(__name__)

TD_STAT = fanduel.TD_STAT  # rushing + receiving TDs as the books price them; split after the fit
# The stats fitted from Prop Markets, in fitting order: receptions first, since they set
# receiving yards' P(0). Their names in "incomplete: missing ..." flags.
MARKET_STATS = {
    "receptions": "receptions",
    "receiving_yards": "receiving yards",
    "rushing_yards": "rushing yards",
    "carries": "carries",
    "passing_yards": "passing yards",
    "passing_tds": "passing TDs",
    "passing_interceptions": "interceptions",
    TD_STAT: "touchdowns",
}
# Usage Stats: fitted for their mean (carries -> expected touches -> fumbles), never simulated or flagged.
USAGE_STATS = ("carries",)
# The market stats a position is expected to have: one missing is an Estimated Stat (flagged
# "estimated"), or flagged "incomplete" and left out when nothing can estimate it. Any other
# stat is fitted when a book prices it (a WR's rushing yards) and otherwise counts as 0.
CORE_STATS = {
    "QB": ("passing_yards", "passing_tds", "passing_interceptions", "rushing_yards", TD_STAT),
    "RB": ("rushing_yards", "receptions", "receiving_yards", TD_STAT),
    "WR": ("receptions", "receiving_yards", TD_STAT),
    "TE": ("receptions", "receiving_yards", TD_STAT),
}
# The market stats _fit_markets fits for a position: what a live run fetches for it. QBs get no
# receiving stats (MODEL_ASSUMPTIONS) and no carries (QB fumbles are a flat rate); only QBs pass.
_SKILL_STATS = ("receptions", "receiving_yards", "rushing_yards", "carries", TD_STAT)
POSITION_STATS = {
    "QB": ("rushing_yards", "passing_yards", "passing_tds", "passing_interceptions", TD_STAT),
    "RB": _SKILL_STATS,
    "WR": _SKILL_STATS,
    "TE": _SKILL_STATS,
    "K": (),  # Game Lines only (Sprint 6); load_live_books fetches kicker markets for the Snapshots
    DST: (),  # Game Lines, team totals and D/ST TD prices: load_live_books(dsts=True)
}


def market_stats_for(positions) -> list[str]:
    """The MARKET_STATS the given positions need, in MARKET_STATS order."""
    wanted = {stat for position in positions for stat in POSITION_STATS[position]}
    return [stat for stat in MARKET_STATS if stat in wanted]


# The vig c a book's market is devigged with when it has no Main Line (one-sided TD prices never do).
PRIOR_C = {TD_STAT: TD_PRIOR_C}
PLAYER_POSITIONS = ("QB", "RB", "WR", "TE")  # fitted from the player's own Prop Markets
MODELED_POSITIONS = (*PLAYER_POSITIONS, "K", DST)
TEAM_POSITIONS = ("K", DST)  # predicted from Game Lines and team markets, not player Prop Markets
# What the model leaves out, listed once in the report's assumptions.
MODEL_ASSUMPTIONS = (
    "Rushing yards count as 0 for a player no book prices them for (most WRs and TEs).",
    "QB receptions and receiving yards are not modelled; they count as 0.",
    "QB fumbles lost are a flat per-game rate, not tied to sacks.",
    'A stat flagged "estimated" has no market for the player: it is estimated from his other stats and recent games. '
    'A stat flagged "incomplete" could not be estimated and counts as 0, so that player\'s range is too low.',
    "Kickers come from the Game Lines alone (his team's Implied Team Total and expected margin): the league's "
    "make rate by distance, no kicker skill, no weather. Kickers' ranges differ little from one to the next.",
    "A D/ST is drawn from past team-games (2015 on) whose opponent had a similar Implied Team Total, tilted to "
    "the opponent's team total and the D/ST TD price where a Book prices them.",
)

NO_MARKETS = "no Prop Markets found for this player"
NO_GAME = "no remaining Game on the schedule"
# A book's Game is the scheduled Game when it has the same two teams and kicks off within this of it.
# Books' kickoffs differ from nflverse's by minutes (FD lists MNF at 01:21Z, nflverse 01:15Z).
KICKOFF_TOLERANCE = timedelta(hours=12)


@dataclass(frozen=True)
class BookOdds:
    """One book's Games, its Prop Markets for MARKET_STATS, and (Sprint 6) its Game Lines and
    team markets (TEAM_POINTS, DST_TDS) for kickers and D/ST."""

    book: str
    games: list[Game]
    markets: list[PropMarket]
    lines: list[GameLine] = field(default_factory=list)
    team_markets: list[PropMarket] = field(default_factory=list)


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
    return f"{player.position} is not supported yet, only {'/'.join(MODELED_POSITIONS)}"


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
    for stat, slug in (("rushing_yards", "rush_yds"), ("carries", "rush_att"), ("passing_yards", "pass_yds"),
                       ("passing_tds", "pass_tds"), ("passing_interceptions", "ints")):
        dk_markets += draftkings.parse_prop_markets(read(f"dk_nfl_{slug}_ladder.json"), read(f"dk_nfl_{slug}_ou.json"), stat)
    tabs = {
        "receiving-props": read("fd_nfl_event_receiving_tab.json"),
        "rushing-props": read("fd_nfl_event_rushing_tab.json"),
        "passing-props": read("fd_nfl_event_passing_tab.json"),
        "td-scorer-props": read("fd_nfl_event_td_scorer_tab.json"),
    }
    fd_markets = [
        market
        for stat in MARKET_STATS
        if stat not in fanduel.NOT_OFFERED
        for market in fanduel.parse_prop_markets(tabs[fanduel.TABS[stat]], stat)
    ]
    receiving_tab = tabs["receiving-props"]
    dk_league, fd_league = read("dk_nfl_league.json"), read("fd_nfl_league.json")
    dk_team = draftkings.parse_team_totals(read("dk_nfl_team_totals.json")) + draftkings.parse_dst_tds(
        read("dk_nfl_td_scorer.json"))
    fd_team = fanduel.parse_team_totals(read("fd_nfl_event_popular_tab.json")) + fanduel.parse_dst_tds(
        tabs["td-scorer-props"])
    return [
        BookOdds(draftkings.BOOK, draftkings.parse_games(ladder), dk_markets, draftkings.parse_game_lines(dk_league),
                 dk_team),
        BookOdds(fanduel.BOOK, fanduel.parse_games(receiving_tab), fd_markets, fanduel.parse_game_lines(fd_league),
                 fd_team),
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
    stats: Sequence[str] | None = None,
    kickers: bool = False,
    dst_games: Sequence[ScheduledGame] = (),
    kicker_ladders: bool = False,
) -> list[BookOdds]:
    """Both books' Prop Markets for `stats` (default MARKET_STATS) in `games`, fetched live.

    Pass `market_stats_for(positions)` so a run with no QB doesn't fetch passing markets.

    With `kickers` or `dst_games` (Sprint 6) each book's Game Lines come too (the league responses
    already carry them). `dst_games`, the Games with a D/ST to predict, adds the team totals
    (DraftKings' "Team Totals"; FanDuel's "popular" tab, one request per Game, for those Games
    only) and the D/ST TD prices. `kicker_ladders` fetches DraftKings' kicker subcategories, only
    so a Snapshot keeps them (PLAN Sprint 6: the model doesn't use them): pass it when saving one.

    The two Books fetch side by side (Sprint 7), each through its own view of `http`, paced by its
    Pacing (`ffp.sources.http.BOOK_PACING`); after its league response, each Book's requests go out as
    one batch. DraftKings serves every Game in one set of subcategory responses per stat, so a stat it
    doesn't list yet costs only that stat. FanDuel needs one request per Game and tab, so only the
    events matching `games` are fetched. A stat or event that fails is logged and left out; the rest
    still count. A Book that fails outright, or pushes back again after backing off, is left out
    with one warning. Ctrl-C stops both Books (`http.stop()`) and is raised.
    """
    stats = list(MARKET_STATS if stats is None else stats)
    on_response = _once(on_response)
    dsts = bool(dst_games)
    jobs = [
        (draftkings.BOOK, "DraftKings",
         lambda view: _draftkings_book(view, stats, kickers, dsts, kicker_ladders, on_response)),
        (fanduel.BOOK, "FanDuel",
         lambda view: _fanduel_book(view, games, now, stats, kickers, dst_games, on_response)),
    ]
    results = _side_by_side([lambda book=book, label=label, job=job: _run_book(http, book, label, job)
                             for book, label, job in jobs], stop=http.stop)
    return [book for book in results if book is not None]


def _run_book(http, book: str, label: str, job) -> "BookOdds | None":
    """One Book's fetch through its own view of `http`, or None with one warning when the Book fails
    outright."""
    try:
        with http.for_book(book) as view:  # paced by the Book's own Pacing (Http.book_pacing)
            return job(view)
    except _book_failures() as error:
        log.warning("%s left out: %s", label, _describe(error))
        return None


def _side_by_side(jobs: Sequence[Callable], stop: Callable[[], None]) -> list:
    """Each job's result, in order, the jobs run at once on daemon threads. Ctrl-C calls `stop` and is
    raised at once: a request still in flight is abandoned (its thread dies with the run), and every wait
    ends. A job's own exception is raised once all have finished."""
    import threading

    results: list = [None] * len(jobs)
    errors: list = [None] * len(jobs)

    def run(i: int) -> None:
        try:
            results[i] = jobs[i]()
        except BaseException as error:  # noqa: BLE001 - handed to the main thread
            errors[i] = error

    threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(jobs))]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            while thread.is_alive():
                thread.join(0.1)  # short joins, so Ctrl-C reaches the main thread at once
    except KeyboardInterrupt:
        stop()
        raise
    for error in errors:
        if error is not None:
            raise error
    return results


def _draftkings_book(http, stats, kickers: bool, dsts: bool, kicker_ladders: bool, on_response) -> "BookOdds | None":
    """DraftKings: the league response, then every stat's and team market's subcategories as one batch."""
    from ffp.sources import live

    failures = _book_failures()
    league = live.fetch_dk_league(http, on_response=on_response)
    requests, by_stat = [], {}
    for stat in stats:
        try:
            by_stat[stat] = live.dk_stat_requests(league, stat)
        except failures as error:
            log.warning("DraftKings %s left out: %s", MARKET_STATS[stat], _describe(error))
            continue
        requests += by_stat[stat]
    subs = [*(live.DK_TEAM_TOTALS, live.DK_TD_SCORER)] if dsts else []
    subs += list(live.DK_KICKER_SUBCATEGORIES) if kicker_ladders else []
    sub_names = {}
    for sub in subs:
        try:
            name, urls = live.dk_subcategory_request(league, sub)
        except failures as error:
            log.warning("DraftKings %s left out: %s", sub.subcategory, _describe(error))
            continue
        sub_names[sub] = name
        requests.append((name, urls))
    got = live.fetch_dk_batch(http, requests, on_response=on_response)

    markets, priced = [], False
    for stat, entries in by_stat.items():
        try:
            payloads = {name: got[name] for name, _ in entries}
            error = next((p for p in payloads.values() if isinstance(p, Exception)), None)
            if error is not None:
                raise error
            slug = live.DK_STATS[stat].slug
            markets += draftkings.parse_prop_markets(payloads[f"dk_{slug}_ladder"], payloads.get(f"dk_{slug}_ou"), stat)
            priced = True
        except failures as error:
            log.warning("DraftKings %s left out: %s", MARKET_STATS[stat], _describe(error))
    lines, team = [], []
    if kickers or dsts:
        try:
            lines = draftkings.parse_game_lines(league)
        except failures as error:
            log.warning("DraftKings Game Lines left out: %s", _describe(error))
    for sub, name in sub_names.items():
        payload = got[name]
        try:
            if isinstance(payload, Exception):
                raise payload
            if sub == live.DK_TEAM_TOTALS:
                team += draftkings.parse_team_totals(payload)
            elif sub == live.DK_TD_SCORER:
                team += draftkings.parse_dst_tds(payload)
        except failures as error:
            log.warning("DraftKings %s left out: %s", sub.subcategory, _describe(error))
    if not (priced or kickers or dsts):
        return None  # nothing it prices parsed: no Games either, as before
    return BookOdds(draftkings.BOOK, draftkings.parse_games(league), markets, lines, team)


def _fanduel_book(http, games, now, stats, kickers: bool, dst_games, on_response) -> "BookOdds":
    """FanDuel: the league page, then every wanted event's tabs as one batch."""
    from ffp.sources import live

    failures = _book_failures()
    league = live.fetch_fanduel_league(http, on_response=on_response)
    fd_games = fanduel.parse_games(league, now)
    lines = fanduel.parse_game_lines(league) if kickers or dst_games else []
    wanted = sorted({g.game_id for g in fd_games for s in games if _same_game(g, s)})
    wanted_dst = {g.game_id for g in fd_games for s in dst_games if _same_game(g, s)}
    prop_tabs = live.fd_tabs(stats)
    dst_tabs = [fanduel.GAME_LINES_TAB, fanduel.TABS[TD_STAT]]
    pages = [(event_id, tab) for event_id in wanted
             for tab in (list(dict.fromkeys([*prop_tabs, *dst_tabs])) if event_id in wanted_dst else prop_tabs)]
    urls = [live.fd_event_url(event_id, tab) for event_id, tab in pages]
    team: list[PropMarket] = []
    markets = []
    for (event_id, tab), url, payload in zip(pages, urls, live.get_many(http, urls)):
        if isinstance(payload, Exception):  # one failed event page costs only its own Game's tab
            log.warning("FanDuel event %s tab %s left out: %s", event_id, tab, _describe(payload))
            continue
        if on_response is not None:
            on_response(fanduel.BOOK, f"fd_event_{event_id}_{tab}", url, payload)
        for stat in stats:  # one stat at a time: a tab holds several (receiving yards, receptions)
            if fanduel.TABS.get(stat) != tab:  # .get: NOT_OFFERED stats have no tab
                continue
            try:
                markets += fanduel.parse_prop_markets(payload, stat)
            except failures as error:
                log.warning("FanDuel event %s %s left out: %s", event_id, MARKET_STATS[stat], _describe(error))
        if event_id in wanted_dst:
            try:
                if tab == fanduel.GAME_LINES_TAB:
                    team += fanduel.parse_team_totals(payload)
                elif tab == fanduel.TABS[TD_STAT]:
                    team += fanduel.parse_dst_tds(payload)
            except failures as error:
                log.warning("FanDuel event %s team markets left out: %s", event_id, _describe(error))
    return BookOdds(fanduel.BOOK, fd_games, markets, lines, team)


def _once(on_response: Callable | None) -> Callable | None:
    """on_response, called once per (book, name, url), one call at a time: the Books fetch on their own
    threads (Sprint 7), and the Snapshot writer numbers its files in call order."""
    import threading

    if on_response is None:
        return None
    seen = set()
    lock = threading.Lock()

    def call(book: str, name: str, url: str, payload) -> None:
        with lock:
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
    td_history: Mapping[str, TdHistory] | None = None,
    chance_thresholds: Sequence[float] = (),
    recent_games: Mapping[str, RecentGames] | None = None,
    jobs: int | None = 1,
) -> tuple[list[PlayerResult], list[SkippedPlayer]]:
    """A Score Distribution for each target with Prop Markets in its Game.

    Each market stat is fitted from every book's markets for the player (S2.5).
    Receptions set receiving yards' P(0); the anytime-TD rate is split into
    rushing and receiving TDs by the position's share; fumbles lost and 2-pt
    come from Base Rates. The stats are sampled together through the
    position's latent correlation matrix, `correlation` "within" (default),
    "pooled" (S3.6) or "none" (independent). The rushing/receiving split of TDs and 2-pt is the
    player's own, from `td_history` (td_share.load_td_history, keyed by gsis_id);
    without it, the position share. `chance_thresholds` adds each player's chance of
    that many points or more. A position's core stat without a market, or whose
    fit fails, is an Estimated Stat (S5.4: from the player's other stats and his
    `recent_games`, estimates.load_recent_games, keyed by gsis_id), flagged
    "estimated"; where no estimate can be made it is left out and flagged "incomplete" (S3.5). With no book pricing the Game at all,
    the player is skipped as "props not posted"; with the Game priced but not
    the player, as "no Prop Markets"; with no fit succeeding, as unfittable.

    With `jobs` above 1 (Sprint 8), the players with markets to fit are predicted in that many worker
    processes; None picks `default_jobs` for them. Each player's prediction is independent and seeded
    on its own, so the results are identical to a one-process run. A worker's Fair Points
    (`on_fair_points`) and log records are replayed here as each player lands, in target order, so a
    crash keeps what came before it. One player to fit is predicted in process (a worker start costs
    about a second), and so are D/STs, kickers and skips.
    """
    context = _Context(books, rules, correlation, chance_thresholds)
    task = {id(t): (t, (td_history or {}).get(t.player.gsis_id), (recent_games or {}).get(t.player.gsis_id))
            for t in targets}
    # Only players with markets to fit are worth a worker: a D/ST, a kicker or a skip is near instant.
    to_fit = [t for t in targets if _needs_fitting(t, books)]
    jobs = default_jobs(len(to_fit)) if jobs is None else jobs
    results, skipped = [], []

    def keep(outcome):
        (results if isinstance(outcome, PlayerResult) else skipped).append(outcome)

    def here(target):  # in this process: logging and the Fair Points go straight through
        report = None if on_fair_points is None else (lambda stat, pts: on_fair_points(target, stat, pts))
        keep(_predict_target(context, *task[id(target)], report))

    if jobs <= 1 or len(to_fit) <= 1:
        for target in targets:
            here(target)
        return results, skipped
    from ffp.workers import in_order

    with _worker_pool(min(jobs, len(to_fit)), context) as pool:
        fitted = in_order(pool, _predict_in_worker, [task[id(t)] for t in to_fit])  # answers in to_fit order
        fitting = {id(t) for t in to_fit}
        for target in targets:
            if id(target) not in fitting:
                here(target)
                continue
            # Replayed as each player lands, in target order: a later crash keeps what came before.
            outcome, fair_points, records, error = next(fitted)
            for name, level, message in records:
                logging.getLogger(name).log(level, "%s", message)
            if on_fair_points is not None:
                for stat, points in fair_points:
                    on_fair_points(target, stat, points)
            if error is not None:
                raise error
            keep(outcome)
    return results, skipped


def _needs_fitting(target: Target, books: Sequence[BookOdds]) -> bool:
    """Whether a target has Prop Markets to fit: the slow part of a prediction."""
    if target.player.position in TEAM_POSITIONS:
        return False
    return has_scoring_markets(_markets_for(target, books)[1])


def default_jobs(players: int, cpus: int | None = None) -> int:
    """Worker processes for predicting `players`: one per player, leaving a fifth of the usable cores (at
    least one) free for the rest of the machine."""
    from ffp.workers import usable_cpus

    cpus = cpus or usable_cpus()
    return max(1, min(players, cpus - max(1, cpus // 5)))


@dataclass(frozen=True)
class _Context:
    """What every player's prediction shares: sent to each worker once, not once per player."""

    books: Sequence[BookOdds]
    rules: ScoringRules
    correlation: str | None
    chance_thresholds: Sequence[float]


class _Records(logging.Handler):
    """In a worker, collects what one player's prediction logs, to be replayed by the main process."""

    def __init__(self):
        super().__init__(logging.NOTSET)
        self.records: list[tuple[str, int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.name, record.levelno, record.getMessage()))


def _predict_target(context: "_Context", target: Target, td_history, recent, report):
    books, rules = context.books, context.rules
    if target.player.position in TEAM_POSITIONS:
        return _team_result(target, books, rules, context.chance_thresholds)
    priced, by_stat = _markets_for(target, books)
    # A Usage Stat alone (a carries line) is nothing to score: the player has no markets.
    if not has_scoring_markets(by_stat):
        return SkippedPlayer(target.name, NO_MARKETS if priced else PROPS_NOT_POSTED)
    share = rushing_td_share(target.player.position, td_history)
    fitted = fit_player(target.player.position, by_stat, share, on_fair_points=report, recent=recent)
    if not fitted.dists:
        return SkippedPlayer(target.name, f"could not fit any of its markets ({'; '.join(fitted.errors)})")
    return _player_result(target, fitted, rules, context.correlation, context.chance_thresholds)


_WORKER_CONTEXT: "_Context | None" = None


def _start_worker(context: "_Context") -> None:
    """Set up a prediction worker: the shared context, and every log record collected (levels are
    filtered when the main process replays them under its own logging setup)."""
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = context
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.DEBUG)


def _predict_in_worker(task):
    """One target in a worker: (outcome, [(stat, Fair Points)], [(logger, level, message)], error). An
    exception comes back as `error` with what was logged before it, for the main process to replay
    and then raise."""
    target, td_history, recent = task
    fair_points: list = []
    handler = _Records()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        outcome = _predict_target(_WORKER_CONTEXT, target, td_history, recent,
                                  lambda stat, pts: fair_points.append((stat, pts)))
        return outcome, fair_points, handler.records, None
    except Exception as error:  # noqa: BLE001 - raised by the main process, in target order
        return None, fair_points, handler.records, error
    finally:
        root.removeHandler(handler)


def _worker_pool(workers: int, context: "_Context"):
    """Worker processes for `predict` (ffp.workers: spawn, Ctrl-C left to this process)."""
    from ffp.workers import process_pool

    return process_pool(workers, _start_worker, (context,))


def has_scoring_markets(by_stat: Mapping[str, Sequence[PropMarket]]) -> bool:
    """Whether any market is for a Scoring Stat: a Usage Stat alone (a carries line) is nothing to score."""
    return any(markets for stat, markets in by_stat.items() if stat not in USAGE_STATS)


@dataclass
class Fitted:
    """One player's fitted market stats: Stat Distributions by Scoring Stat, their report
    details (what each was fitted from), and the "incomplete" flags and fit errors."""

    dists: dict[str, StatDistribution]
    details: dict[str, StatDetail]
    flags: list[str]
    errors: list[str]
    rushing_share: float  # the player's rushing share of TDs, which also splits his 2-pt
    usage: dict[str, StatDistribution]  # Usage Stats (carries): only their means are used
    tds: StatDistribution | None = None  # rush + rec TDs as fitted, before the split
    estimated: list[str] = field(default_factory=list)  # the core stats that are Estimated Stats


def fit_player(
    position: str,
    by_stat: Mapping[str, Sequence[PropMarket]],
    share: float,
    prior_c: Mapping[str, float] | None = None,
    on_fair_points: Callable[[str, list[FairPoint]], None] | None = None,
    recent: RecentGames | None = None,
    estimate: bool = True,
) -> Fitted:
    """Fit one player's market stats from his Prop Markets by stat (any books), for `position`.

    Each stat's markets are devigged by `fair_points` (a book's vig from its Main Line,
    else from `prior_c[book]`, else the stat's prior: TD_PRIOR_C for TDs); pass
    prior_c={"kalshi": KALSHI_PRIOR_C} for an exchange. Receptions set receiving yards'
    P(0); carries are a Usage Stat; the TD total is split by `share` (td_share).
    `on_fair_points(stat, points)` sees each stat's Fair Probabilities.

    A core stat with no market, or whose fit fails, becomes an Estimated Stat when at least one
    Scoring Stat was fitted from a market: its mean from the player's other stats and his
    `recent` games (ffp.estimates), flagged "estimated: ...". Where no estimate can be made, or
    with `estimate` False, it is left out and flagged "incomplete: ...".
    """
    core = CORE_STATS[position]
    fitted = Fitted({}, {}, [], [], share, {})
    gaps: dict[str, str] = {}  # core stat -> why it has no market fit (the text of its flag)

    def fit(stat: str, fitter):
        markets = by_stat.get(stat)
        label = MARKET_STATS[stat]
        if not markets:
            if stat in core:
                gaps[stat] = f"missing {label}"
            return None
        points = fair_points(markets, prior_c=prior_c, default_c=PRIOR_C.get(stat, DEFAULT_PRIOR_C))
        if on_fair_points is not None:
            on_fair_points(stat, points)
        try:
            dist = fitter(points)
        except FitError as error:  # a bug or bad configuration raises something else and stops the run
            if stat not in USAGE_STATS:
                gaps[stat] = f"could not fit {label} ({error})"
                fitted.errors.append(f"{label}: {error}")
            return None
        if stat in USAGE_STATS:
            fitted.usage[stat] = dist
        else:
            fitted.dists[stat] = dist
            fitted.details[stat] = _detail(dist, markets)
        return dist

    if position != "QB":  # QB receptions and receiving yards are not modelled (MODEL_ASSUMPTIONS)
        receptions = fit("receptions", lambda pts: fit_receptions(pts, position))
        # No catch, no yards (research Q4): with a receptions fit, P(0 yards) is P(0 receptions).
        pi0 = None if receptions is None else prob_zero(receptions)
        fit("receiving_yards", lambda pts: fit_receiving_yards(pts, position, pi0=pi0))
    fit("rushing_yards", lambda pts: fit_rushing_yards(pts, position))
    if position != "QB":  # carries only set touches for fumbles, and QB fumbles are a flat rate
        fit("carries", lambda pts: fit_carries(pts, position))
    if position == "QB":
        fit("passing_yards", lambda pts: fit_passing_yards(pts, position))
        fit("passing_tds", fit_passing_tds)
        fit("passing_interceptions", fit_interceptions)
    tds = fit(TD_STAT, fit_touchdowns)
    tds_detail = fitted.details.pop(TD_STAT, None)
    fitted.dists.pop(TD_STAT, None)
    if estimate and (fitted.dists or tds is not None):  # only for a player the market otherwise prices
        tds, tds_detail = _estimate_gaps(position, fitted, gaps, tds, tds_detail, recent)
    fitted.flags += [f"incomplete: {why}" for why in gaps.values()]
    if tds is not None:
        fitted.tds = tds
        rushing, receiving = split_touchdowns(tds, share)
        for stat, dist in (("receiving_tds", receiving), ("rushing_tds", rushing)):
            fitted.dists[stat] = dist
            fitted.details[stat] = replace(tds_detail, mean=float(dist.mean), p50=_median(dist))
    return fitted


def _estimate_gaps(
    position: str,
    fitted: Fitted,
    gaps: dict[str, str],
    tds: StatDistribution | None,
    tds_detail: StatDetail | None,
    recent: RecentGames | None,
) -> tuple[StatDistribution | None, StatDetail | None]:
    """Fill each core stat in `gaps` with an Estimated Stat where one can be made (S5.4), flag it
    "estimated: ..." and take it out of `gaps`. Returns the TD total and its detail, estimated
    when TDs were the gap. Stats are estimated in fitting order, each from the means known so
    far, so receptions can set estimated receiving yards' P(0) and yards can set TDs."""
    means = {stat: float(d.mean) for stat, d in {**fitted.dists, **fitted.usage}.items()}
    if tds is not None:
        means[TD_STAT] = float(tds.mean)
        means[RUSHING_TDS] = float(tds.mean) * fitted.rushing_share  # rushing yards come from these
    for stat in [s for s in MARKET_STATS if s in gaps]:
        mean = estimate_mean(stat, position, means, recent)
        dist = None if mean is None else estimated_distribution(stat, position, mean, fitted.dists.get("receptions"))
        if dist is None:
            continue
        why = gaps.pop(stat)
        reason = "no market" if why.startswith("missing") else "could not fit its market"
        fitted.flags.append(f"estimated: {MARKET_STATS[stat]} ({reason})")
        fitted.estimated.append(stat)
        means[stat] = float(dist.mean)
        detail = StatDetail(mean=float(dist.mean), p50=_median(dist), books=[], rungs=0)
        if stat == TD_STAT:
            tds, tds_detail = dist, detail
        else:
            fitted.dists[stat], fitted.details[stat] = dist, detail
    return tds, tds_detail


def _median(dist: StatDistribution) -> float:
    return float(np.asarray(dist.ppf(np.array([0.5])))[0])


def _detail(dist: StatDistribution, markets: list[PropMarket]) -> StatDetail:
    return StatDetail(
        mean=float(dist.mean),
        p50=_median(dist),
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


def stat_distributions(position: str, fitted: Fitted) -> dict[str, StatDistribution]:
    """The Stat Distributions to simulate: the fitted Scoring Stats plus the position's Base Rates
    (fumbles lost from expected touches, 2-pt split by the player's TD share)."""
    receptions, carries = fitted.dists.get("receptions"), fitted.usage.get("carries")
    rushing_yards = fitted.dists.get("rushing_yards")
    stat_dists = dict(fitted.dists)
    stat_dists.update(base_rate_stats(
        position,
        receptions_mean=None if receptions is None else receptions.mean,
        carries_mean=None if carries is None else carries.mean,
        rushing_share=fitted.rushing_share,
        rushing_yards_mean=None if rushing_yards is None else rushing_yards.mean,
    ))
    return stat_dists


NO_CORRELATION = "none"  # the `correlation` kind that samples every stat independently


def score_player(
    position: str,
    fitted: Fitted,
    rules: ScoringRules,
    correlation: str | None = None,
    n: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> ScoreDistribution:
    """The player's Score Distribution: `stat_distributions` simulated through the position's
    latent correlation matrix `correlation` ("within" by default, "pooled"), or independently
    for "none"."""
    corr = None if correlation == NO_CORRELATION else params.correlation(position, correlation)
    return simulate_scores(stat_distributions(position, fitted), rules, n=n, seed=seed, corr=corr)


def _player_result(
    target: Target, fitted: Fitted, rules: ScoringRules, correlation: str | None, chance_thresholds: Sequence[float] = ()
) -> PlayerResult:
    position = target.player.position
    scores = score_player(position, fitted, rules, correlation)
    game = target.game
    return PlayerResult(
        player=target.name,
        team=target.player.team,
        game=f"{game.away} @ {game.home}",
        kickoff=game.kickoff,
        position=position,
        percentiles=scores.percentiles(),
        flags=fitted.flags,
        stats=fitted.details,
        chances=chances(scores, chance_thresholds),
    )


# --- Kickers and D/ST (Sprint 6) -----------------------------------------------------

FROM_GAME_LINES = "from game lines"
NO_GAME_LINES = "no Game Lines for this Game yet (no Book lists a spread and total, nor does nflverse)"
NO_TEAM_MARKETS = "from game lines (no team total or D/ST TD market)"


def _game_lines(target: Target, books: Sequence[BookOdds]):
    """(Implied Team Totals or None, the team markets in the target's Game, by book)."""
    lines, markets = [], []
    for book in books:
        games = {g.game_id: g for g in book.games if _same_game(g, target.game)}
        lines += [(games[line.game_id].home, games[line.game_id].away, line)
                  for line in book.lines if line.game_id in games]
        markets += [m for m in book.team_markets if m.game_id in games]
    return gamelines.implied_totals(target.game, lines), markets


def _team_result(target: Target, books: Sequence[BookOdds], rules: ScoringRules,
                 chance_thresholds: Sequence[float]) -> PlayerResult | SkippedPlayer:
    """A kicker's or D/ST's Score Distribution from its Game Lines (and, for a D/ST, the opponent's
    team total and its own TD prices), or why there is none."""
    position, team, game = target.player.position, target.player.team, target.game
    section = "kicker" if position == "K" else "dst"
    if not getattr(rules, section):
        return SkippedPlayer(target.name, f"the Scoring Rules ({rules.name}) have no {section} section")
    totals, markets = _game_lines(target, books)
    if totals is None:
        return SkippedPlayer(target.name, NO_GAME_LINES)
    opponent = game.opponent(team)
    flags = [] if totals.sources != [gamelines.NFLVERSE] else ["Game Lines from nflverse's schedule (no Book lists them)"]
    if position == "K":
        scores, k = kicker_scores(totals.by_team[team], totals.expected_margin(team), game.season, rules)
        flags.insert(0, FROM_GAME_LINES)
        stats = {"fg_attempts": _sample_detail(k.fga, totals.sources),
                 "fg_made": _sample_detail(np.sum(~np.isnan(k.fg_made_yards), axis=1), totals.sources),
                 "pat_made": _sample_detail(k.pat_made, totals.sources)}
    else:
        opp_points = gamelines.team_markets(markets, opponent, TEAM_POINTS)
        tds = gamelines.team_markets(markets, team, DST_TDS)
        result = dst_scores(totals.by_team[opponent], game.season, rules,
                            opponent_points=fair_points(opp_points) if opp_points else (),
                            dst_tds=fair_points(tds, default_c=TD_PRIOR_C) if tds else ())
        scores, drawn = result.scores, result.sampled
        if not opp_points and not tds:
            flags.insert(0, NO_TEAM_MARKETS)
        stats = {"points_allowed": _sample_detail(drawn["points_allowed"], _books(opp_points) or totals.sources,
                                                  _rungs(opp_points)),
                 "sacks": _sample_detail(drawn["sacks"], totals.sources),
                 "interceptions": _sample_detail(drawn["interceptions"], totals.sources),
                 "dst_tds": _sample_detail(drawn["dst_tds"], _books(tds) or totals.sources, _rungs(tds))}
    return PlayerResult(
        player=target.name, team=team, game=f"{game.away} @ {game.home}", kickoff=game.kickoff,
        position="D/ST" if position == DST else position, percentiles=scores.percentiles(), flags=flags,
        stats=stats, chances=chances(scores, chance_thresholds),
    )


def _books(markets: Sequence[PropMarket]) -> list[str]:
    return sorted({m.book for m in markets})


def _rungs(markets: Sequence[PropMarket]) -> int:
    return sum(len(m.ladder) + (m.main_line is not None) for m in markets)


def _sample_detail(values, books: Sequence[str], rungs: int = 0) -> StatDetail:
    values = np.asarray(values, dtype=float)
    return StatDetail(mean=float(values.mean()), p50=float(np.quantile(values, 0.5, method="inverted_cdf")),
                      books=list(books), rungs=rungs)


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))
