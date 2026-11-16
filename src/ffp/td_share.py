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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from ffp import params as _params
from ffp.base_rates import POSITIONS, td_rushing_share

log = logging.getLogger(__name__)

Row = Mapping[str, object]
Fetch = Callable[[int], Iterable[Row]]

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


def load_td_history(season: int, fetch: Fetch | None = None, params: Mapping | None = None) -> dict[str, TdHistory]:
    """Every player's TdHistory over `season` to date + the previous `prior_seasons`, by gsis_id.

    `fetch(season)` returns nflverse `load_player_stats` weekly rows; it defaults to
    nflreadpy (network, cached by nflreadpy). Every row counts (REG and POST); nulls
    count as 0. The rows' `position` is not used: nflverse puts the player's current
    position on every row. Before week 1 the current season's file doesn't exist
    yet (a 404 ConnectionError): that season simply has no rows. Any other failure
    is logged and gives no history at all, so every player gets the position share.
    """
    fetch = fetch or _nflreadpy_player_stats
    prior = int(_load(params)["td_share"]["prior_seasons"])
    totals: dict[str, list[float]] = {}
    try:
        for s in range(season - prior, season + 1):
            try:
                rows = fetch(s)
            except ConnectionError as e:
                if s == season and "404" in str(e):
                    continue  # the season hasn't started: no file yet
                raise
            for row in rows:
                pid = row.get("player_id")
                if not _present(pid):
                    continue
                acc = totals.setdefault(str(pid), [0.0] * len(_COUNTS))
                for i, col in enumerate(_COUNTS):
                    acc[i] += _count(row.get(col))
    except Exception as e:  # noqa: BLE001 - any data failure degrades to the position share
        log.warning("TD history unavailable (%s); every player's rushing TD share is the position share", e)
        return {}
    return {pid: TdHistory(*acc) for pid, acc in totals.items()}


def _present(value: object) -> bool:
    if value is None or value != value:
        return False
    return str(value).strip().lower() not in ("", "none", "nan", "null")


def _count(value: object) -> float:
    """A stat from a polars row (number or None) or a CSV row (text, "" or "NA")."""
    if not _present(value) or str(value).strip().upper() == "NA":
        return 0.0
    return float(value)


def _nflreadpy_player_stats(season: int) -> list[dict]:  # pragma: no cover - network
    import nflreadpy  # imported here so tests and offline runs never need it

    cols = ["player_id", *_COUNTS]
    return nflreadpy.load_player_stats(season).select(cols).to_dicts()
