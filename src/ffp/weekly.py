"""nflverse weekly player stats: one loader, indexed by player and week.

The TD history (`ffp.td_share`), the recent games behind Estimated Stats (`ffp.estimates`)
and the Backtest's outcomes all read nflverse `load_player_stats` weekly rows. This module
fetches them once per season (`memoized`, `nflreadpy_player_stats`), skips a current
season whose file doesn't exist yet (`fetch_seasons`), and answers windowed questions with
an index built once (`WeeklyStats`) instead of rescanning every row per question.

nflverse has a row only for a game in which the player recorded a stat. A game he played
without one (a blocking TE, a back with no touches) comes from snap counts: pass those
games as `played` (`played_games`), so per-game averages count them.
"""

import bisect
import math
from collections.abc import Callable, Iterable, Mapping, Sequence, Set

from ffp.players import _present

Row = Mapping[str, object]
Fetch = Callable[[int], Iterable[Row]]
Before = tuple[int, int]  # (season, week): only weeks strictly before it count


def number(value: object) -> float:
    """A stat from a polars row (number, None or NaN) or a CSV row (text, "" or "NA"): missing is 0."""
    if not _present(value) or str(value).strip().upper() in ("NA", "NAN"):
        return 0.0
    out = float(value)
    return 0.0 if math.isnan(out) else out


def game_week(game_id: str) -> tuple[int, int]:
    """(season, week) from an nflverse game_id such as "2026_10_LAC_BAL"."""
    season, week = str(game_id).split("_")[:2]
    return int(season), int(week)


def fetch_seasons(fetch: Fetch, seasons: Iterable[int], current: int) -> dict[int, list[Row]]:
    """Each season's rows. Before week 1 the `current` season's file doesn't exist yet (a 404
    ConnectionError): it simply has no rows. Any other failure raises."""
    out = {}
    for season in seasons:
        try:
            out[season] = list(fetch(season))
        except ConnectionError as e:
            if season == current and "404" in str(e):
                continue
            raise
    return out


def memoized(fetch: Fetch) -> Fetch:
    """`fetch`, calling through once per season: share it between loaders in one run."""
    cache: dict[int, list[Row]] = {}

    def once(season: int) -> list[Row]:
        if season not in cache:
            cache[season] = list(fetch(season))
        return cache[season]

    return once


# The weekly columns any reader uses (ffp.outcomes.COLUMNS, ffp.estimates.COLUMNS,
# ffp.td_share._COUNTS, the Backtest's carry shares by team, plus the keys): the file has over
# 100, and several seasons of them as dicts would be held in memory. tests/test_td_share.py
# checks the readers stay covered.
PLAYER_STATS_COLUMNS = (
    "player_id", "game_id", "season", "week", "team",
    "passing_yards", "passing_tds", "passing_interceptions", "passing_2pt_conversions",
    "carries", "rushing_yards", "rushing_tds", "rushing_2pt_conversions",
    "targets", "receptions", "receiving_yards", "receiving_tds", "receiving_2pt_conversions",
    "sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost",
)


def nflreadpy_player_stats(season: int) -> list[dict]:  # pragma: no cover - network
    """nflverse weekly player stats for `season`, PLAYER_STATS_COLUMNS only (network; nflreadpy
    caches on disk)."""
    import nflreadpy  # imported here so tests and offline runs never need it

    return nflreadpy.load_player_stats(season, summary_level="week").select(PLAYER_STATS_COLUMNS).to_dicts()


def played_games(snaps: Iterable[Mapping], roster: Iterable[Mapping]) -> set[tuple[str, str]]:
    """(game_id, gsis_id) of every player with an offensive snap, from nflverse snap counts
    (keyed by PFR id) and roster rows mapping `pfr_id` to `gsis_id`."""
    gsis = {str(r["pfr_id"]): str(r["gsis_id"]) for r in roster if _present(r.get("pfr_id")) and _present(r.get("gsis_id"))}
    return {(str(s["game_id"]), gsis[str(s["pfr_player_id"])]) for s in snaps
            if number(s.get("offense_snaps")) > 0 and str(s.get("pfr_player_id")) in gsis}


def load_played(seasons: Iterable[int], snaps_fetch: Fetch | None = None,
                rosters_fetch: Fetch | None = None) -> set[tuple[str, str]]:
    """`played_games` for `seasons`, from nflverse snap counts and weekly rosters (network by
    default). The last season may not have started: it then has no games."""
    seasons = list(seasons)
    snaps = fetch_seasons(snaps_fetch or _nflreadpy_snap_counts, seasons, max(seasons))
    rosters = fetch_seasons(rosters_fetch or nflreadpy_rosters_weekly, seasons, max(seasons))
    return played_games([r for rows in snaps.values() for r in rows], [r for rows in rosters.values() for r in rows])


def nflreadpy_rosters_weekly(season: int) -> list[dict]:  # pragma: no cover - network
    import nflreadpy

    cols = ["season", "week", "gsis_id", "position", "pfr_id"]
    return nflreadpy.load_rosters_weekly(season).select(cols).to_dicts()


def _nflreadpy_snap_counts(season: int) -> list[dict]:  # pragma: no cover - network
    import nflreadpy

    return nflreadpy.load_snap_counts(season).select(["game_id", "pfr_player_id", "offense_snaps"]).to_dicts()


class WeeklyStats:
    """Every player's games in week order, with each game's stat row (empty for a game in
    `played` that has no stat row). Built once; `totals` and `last_games` then answer any
    window by bisection."""

    def __init__(self, rows: Iterable[Row], played: Set[tuple[str, str]] = frozenset()):
        games: dict[str, dict[tuple[int, int], Row]] = {}
        for r in rows:
            pid = r.get("player_id")
            if _present(pid):
                games.setdefault(str(pid), {})[(int(r["season"]), int(r["week"]))] = r
        for game_id, pid in played:
            games.setdefault(pid, {}).setdefault(game_week(game_id), {})
        self._games = {pid: sorted(g.items()) for pid, g in games.items()}
        self._weeks = {pid: [w for w, _ in g] for pid, g in self._games.items()}

    def _span(self, pid: str, since_season: int, before: Before | None) -> tuple[int, int]:
        weeks = self._weeks[pid]
        start = bisect.bisect_left(weeks, (since_season, -1))
        end = len(weeks) if before is None else bisect.bisect_left(weeks, before)
        return start, max(start, end)

    def totals(self, columns: Sequence[str], since_season: int, before: Before | None = None) -> dict[str, list[float]]:
        """Per player with any game in the window, each column summed over his games from
        `since_season` to before `before`."""
        out = {}
        for pid, games in self._games.items():
            start, end = self._span(pid, since_season, before)
            if start == end:
                continue
            out[pid] = [sum(number(row.get(c)) for _, row in games[start:end]) for c in columns]
        return out

    def last_games(self, columns: Sequence[str], n: int, since_season: int,
                   before: Before | None = None, *, from_season: int | None = None,
                   earlier_weight: float = 1.0) -> dict[str, tuple[float, list[float]]]:
        """Per player with any game in the window: (games, column totals) over his last `n`
        games from `since_season` to before `before`, played games without a stat row included.

        A game in a season before `from_season` counts `earlier_weight` times, in both the game
        count and the totals (a last-season game says less about this season's role)."""
        out = {}
        for pid, games in self._games.items():
            start, end = self._span(pid, since_season, before)
            last = games[max(start, end - n):end]
            if last:
                w = [earlier_weight if from_season is not None and season < from_season else 1.0
                     for (season, _), _ in last]
                out[pid] = (sum(w), [sum(wi * number(row.get(c)) for wi, (_, row) in zip(w, last)) for c in columns])
        return out
