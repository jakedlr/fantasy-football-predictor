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
from collections.abc import Mapping, Set
from dataclasses import dataclass, field

from ffp import distributions as dist
from ffp import params as _params
from ffp import weekly
from ffp.distributions import FitError, StatDistribution, prob_zero

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

Fetch = weekly.Fetch


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
    season: int,
    fetch: Fetch | None = None,
    before: tuple[int, int] | None = None,
    n: int = RECENT_GAMES,
    played: Set[tuple[str, str]] = frozenset(),
) -> dict[str, RecentGames]:
    """Every player's last `n` games in `season` to date and the season before, by gsis_id.

    `fetch(season)` returns nflverse `load_player_stats` weekly rows (default nflreadpy,
    network, cached). nflverse has a row only for a game with a stat in it, so pass the
    games he played as `played` (`weekly.load_played`, from snap counts): a game without
    a stat row then counts as a game with zeros, instead of being skipped and inflating
    his averages. `before=(season, week)` keeps only games strictly before that week, for
    a Backtest. Before week 1 the current season's file doesn't exist: it has no rows. Any
    other failure is logged and gives no recent games, so estimates fall back to the
    other priced stats.
    """
    try:
        rows = weekly.fetch_seasons(fetch or weekly.nflreadpy_player_stats, (season - 1, season), season)
        stats = weekly.WeeklyStats((r for season_rows in rows.values() for r in season_rows), played)
    except Exception as e:  # noqa: BLE001 - any data failure degrades to estimates from priced stats
        log.warning("recent games unavailable (%s); Estimated Stats use the other priced stats only", e)
        return {}
    return recent_games(stats, season, before, n)


def recent_games(stats: "weekly.WeeklyStats", season: int, before: tuple[int, int] | None = None,
                 n: int = RECENT_GAMES) -> dict[str, RecentGames]:
    """`load_recent_games` from an already built index (the Backtest asks once per week)."""
    columns = [c for cols in COLUMNS.values() for c in cols]
    out = {}
    for pid, (games, sums) in stats.last_games(columns, n, season - 1, before).items():
        by_column = dict(zip(columns, sums))
        out[pid] = RecentGames(games, {stat: sum(by_column[c] for c in cols) for stat, cols in COLUMNS.items()})
    return out
