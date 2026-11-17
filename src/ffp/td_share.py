"""The per-player rushing TD share: splits an anytime-TD rate (and the 2-pt Base Rate).

research/nflverse-data.md section 6 (S4.3). From the player's own history over this
season to date + the previous `prior_seasons` seasons (REG + POST, unweighted, at any
position he played):

    v     = (ρr·C + κ·m) / (ρr·C + ρt·T + κ)     # volume share, shrunk to the position share m
    share = (R + k·v) / (N + k)                   # TD history, shrunk to v

C, T = carries, targets; R = rushing TDs; N = rushing + receiving TDs. m is the
position's `td_rushing_share` Base Rate, ρr / ρt the position's rushing TDs per carry
and receiving TDs per target, all for the player's current (roster) position, from
`params/base_rates.json` ("td_share"). No history gives v = m and share = m.

The history is nflverse weekly player stats, fetched at runtime (the current season
to date is worth having, and a build-time file would go stale every week). Load it
once per run with `load_td_history` and look players up by gsis_id.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from ffp import params as _params
from ffp import weekly
from ffp.base_rates import POSITIONS, td_rushing_share

log = logging.getLogger(__name__)

Fetch = weekly.Fetch

_COUNTS = ("carries", "targets", "rushing_tds", "receiving_tds")


@dataclass(frozen=True)
class TdHistory:
    """One player's totals over the window: the counts the share is built from."""

    carries: float = 0.0
    targets: float = 0.0
    rushing_tds: float = 0.0
    receiving_tds: float = 0.0


def _load(params: Mapping | None) -> Mapping:
    return _params.load("base_rates") if params is None else params


def rushing_td_share(position: str, history: TdHistory | None = None, params: Mapping | None = None) -> float:
    """The share of the player's rush+rec TDs (and 2-pt conversions) scored rushing.

    `position` is the player's current roster position; `history` None (no rows in the
    window, or nflverse unreachable) gives the position share m.
    """
    if position not in POSITIONS:
        raise ValueError(f"no TD share for position {position!r} (known: {', '.join(POSITIONS)})")
    p = _load(params)
    m = td_rushing_share(position, p)
    h = history or TdHistory()
    cfg = p["td_share"]
    rush = (cfg["rho_rush"][position] or 0.0) * h.carries
    rec = (cfg["rho_rec"][position] or 0.0) * h.targets
    kappa, k = cfg["kappa"], cfg["k"]
    v = (rush + kappa * m) / (rush + rec + kappa)
    return float((h.rushing_tds + k * v) / (h.rushing_tds + h.receiving_tds + k))


def load_td_history(
    season: int, fetch: Fetch | None = None, params: Mapping | None = None, before: tuple[int, int] | None = None
) -> dict[str, TdHistory]:
    """Every player's TdHistory over `season` to date + the previous `prior_seasons`, by gsis_id.

    `before=(season, week)` keeps only rows strictly before that week (the rows' own
    `season` and `week`), so a Backtest of a past Game sees no stats from it or later.

    `fetch(season)` returns nflverse `load_player_stats` weekly rows; it defaults to
    nflreadpy (network, cached by nflreadpy; share one `weekly.memoized` fetch between
    loaders). Every row counts (REG and POST); nulls count as 0. The rows' `position` is
    not used: nflverse puts the player's current position on every row. Before week 1 the
    current season's file doesn't exist yet: that season simply has no rows. Any other
    failure is logged and gives no history at all, so every player gets the position share.
    """
    prior = int(_load(params)["td_share"]["prior_seasons"])
    try:
        rows = weekly.fetch_seasons(fetch or weekly.nflreadpy_player_stats, range(season - prior, season + 1), season)
        stats = weekly.WeeklyStats(r for season_rows in rows.values() for r in season_rows)
    except Exception as e:  # noqa: BLE001 - any data failure degrades to the position share
        log.warning("TD history unavailable (%s); every player's rushing TD share is the position share", e)
        return {}
    return td_histories(stats, season, before, params)


def td_histories(stats: "weekly.WeeklyStats", season: int, before: tuple[int, int] | None = None,
                 params: Mapping | None = None) -> dict[str, TdHistory]:
    """`load_td_history` from an already built index (the Backtest asks once per week)."""
    prior = int(_load(params)["td_share"]["prior_seasons"])
    return {pid: TdHistory(*t) for pid, t in stats.totals(_COUNTS, season - prior, before).items()}
