"""Estimated Stats: a core Scoring Stat with no Prop Market for this player in this Game.

Its mean is estimated two ways and blended (S5.4; research/backtest-kalshi.md compares them
by hiding stats the market did price):

- (a) from another stat the market priced, times the position's pooled ratio between the two
  (`stat_ratios` in params/base_rates.json, built by scripts/build_params.py), e.g. receptions
  = receiving yards × receptions per receiving yard; TDs sum a rushing and a receiving part;
- (b) from the player's own recent games (the last RECENT_GAMES he played, this season and
  last), shrunk toward (a) by SHRINK_GAMES pseudo-games.

Both: their average. One: that one. Neither: no estimate, and the stat stays "incomplete".
The Stat Distribution around the mean is the stat's family on its dispersion relation.
"""

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from ffp import distributions as dist
from ffp import params as _params
from ffp.distributions import FitError, StatDistribution, prob_zero
from ffp.players import _present

log = logging.getLogger(__name__)

RECENT_GAMES = 8  # the player's last games that (b) averages
SHRINK_GAMES = 2.0  # pseudo-games of (a) that (b) is shrunk toward
TD_STAT = "rush_rec_tds"

# Each estimable stat -> the nflverse weekly columns it sums.
COLUMNS = {
    "receptions": ("receptions",),
    "receiving_yards": ("receiving_yards",),
    "rushing_yards": ("rushing_yards",),
    "carries": ("carries",),
    "passing_yards": ("passing_yards",),
    "passing_tds": ("passing_tds",),
    "passing_interceptions": ("passing_interceptions",),
    TD_STAT: ("rushing_tds", "receiving_tds"),
}
# Stats whose (a) estimate sums its sources (TDs = rushing TDs per rushing yard × rushing yards
# + receiving TDs per receiving yard × receiving yards); the others take the first known source.
_SUM_SOURCES = (TD_STAT,)

Row = Mapping[str, object]
Fetch = Callable[[int], Iterable[Row]]


@dataclass(frozen=True)
class RecentGames:
    """One player's totals over his last `games` games."""

    games: int
    totals: Mapping[str, float] = field(default_factory=dict)

    def mean(self, stat: str) -> float:
        return self.totals.get(stat, 0.0) / self.games


def estimate_mean(
    stat: str,
    position: str,
    market_means: Mapping[str, float],
    recent: RecentGames | None = None,
    params: Mapping | None = None,
) -> float | None:
    """The estimated mean of `stat`, or None with nothing to go on.

    `market_means` are the means of the player's stats known so far: those fitted from Prop
    Markets (TDs under "rush_rec_tds", carries included) and any already estimated; `recent`
    the player's recent games.
    """
    ratios = (_params.load("base_rates") if params is None else params)["stat_ratios"].get(stat, {})
    from_markets = [
        ratio * market_means[source]
        for source, by_position in ratios.items()
        if source in market_means and (ratio := by_position.get(position)) is not None
    ]
    a = None
    if from_markets:
        a = sum(from_markets) if stat in _SUM_SOURCES else from_markets[0]
    if recent is None or recent.games <= 0:
        return a
    if a is None:
        return recent.mean(stat)
    b = (recent.totals.get(stat, 0.0) + SHRINK_GAMES * a) / (recent.games + SHRINK_GAMES)
    return (a + b) / 2


def estimated_distribution(
    stat: str, position: str, mean: float, receptions: StatDistribution | None = None
) -> StatDistribution | None:
    """The Stat Distribution of an Estimated Stat: the stat's family at `mean`, on its dispersion
    relation. Receiving yards take P(0) from `receptions` when given (no catch, no yards).
    None when the model has no distribution with that mean."""
    try:
        if not mean > 0:
            return None
        if stat == "receptions":
            return dist._count_on_relation(dist.RECEPTIONS, mean, position)
        if stat == "receiving_yards":
            pi0 = None if receptions is None else prob_zero(receptions)
            return dist.receiving_yards(mean, dist.receiving_yards_sd_relation(mean, position, pi0), position, pi0)
        if stat == "rushing_yards":
            return dist.rushing_yards(mean, dist.rushing_yards_sd_relation(mean, position), position)
        if stat == "passing_yards":
            return dist.passing_yards(mean, dist.passing_yards_sd_relation(mean, position), position)
        if stat == "passing_tds":
            return dist.Binomial(dist.PASS_TD_ATTEMPTS, mean)
        if stat in ("passing_interceptions", TD_STAT):
            return dist.Poisson(mean)
    except (ValueError, FitError):  # FitError is a ValueError: the mean is outside the model's range
        return None
    raise ValueError(f"no Estimated Stat model for {stat!r}")


def load_recent_games(
    season: int, fetch: Fetch | None = None, before: tuple[int, int] | None = None, n: int = RECENT_GAMES
) -> dict[str, RecentGames]:
    """Every player's last `n` games in `season` to date and the season before, by gsis_id.

    `fetch(season)` returns nflverse `load_player_stats` weekly rows (default nflreadpy,
    network, cached); a row is a game the player recorded a stat in. `before=(season, week)`
    keeps only rows strictly before that week, for a Backtest. Before week 1 the current
    season's file doesn't exist (a 404): it has no rows. Any other failure is logged and
    gives no recent games, so estimates fall back to the other priced stats.
    """
    fetch = fetch or _nflreadpy_player_stats
    games: dict[str, list[tuple[tuple[int, int], Row]]] = {}
    try:
        for s in (season - 1, season):
            try:
                rows = fetch(s)
            except ConnectionError as e:
                if s == season and "404" in str(e):
                    continue
                raise
            for row in rows:
                pid = row.get("player_id")
                if not _present(pid):
                    continue
                when = (int(row["season"]), int(row["week"]))
                if before is None or when < before:
                    games.setdefault(str(pid), []).append((when, row))
    except Exception as e:  # noqa: BLE001 - any data failure degrades to estimates from priced stats
        log.warning("recent games unavailable (%s); Estimated Stats use the other priced stats only", e)
        return {}
    out = {}
    for pid, played in games.items():
        last = [row for _, row in sorted(played, key=lambda g: g[0])[-n:]]
        totals = {stat: sum(_number(row.get(c)) for row in last for c in cols) for stat, cols in COLUMNS.items()}
        out[pid] = RecentGames(len(last), totals)
    return out


def _number(value: object) -> float:
    """A stat from a polars row (number or None) or a CSV row (text, "" or "NA")."""
    if not _present(value) or str(value).strip().upper() == "NA":
        return 0.0
    return float(value)


def _nflreadpy_player_stats(season: int) -> list[dict]:  # pragma: no cover - network
    import nflreadpy  # imported here so tests and offline runs never need it

    cols = ["player_id", "season", "week", *sorted({c for cs in COLUMNS.values() for c in cs})]
    return nflreadpy.load_player_stats(season).select(cols).to_dicts()
