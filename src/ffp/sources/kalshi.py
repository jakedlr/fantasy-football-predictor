"""Kalshi's settled NFL player-prop markets → Backtest inputs (PLAN S5.1).

Market semantics, the price rule and the drop rules are from research/kalshi.md.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from .fanduel import TD_STAT

BOOK = "kalshi"

# Kalshi series → Scoring Stat or Usage Stat. Each is a Ladder: one binary market per Rung,
# "X+" (floor_strike X − 0.5). KXNFLTD (2026) is a 1+/2+/3+ TD Ladder; in 2025 the same
# events were KXNFLANYTD (1+) and KXNFL2TD (2+), one market per player. The PROT, LADDER,
# ESCALATOR and PRECISE variants are not used.
LADDER_SERIES: dict[str, str] = {
    "KXNFLRECYDS": "receiving_yards",
    "KXNFLREC": "receptions",
    "KXNFLRSHYDS": "rushing_yards",
    "KXNFLRSHATT": "carries",
    "KXNFLPASSYDS": "passing_yards",
    "KXNFLPASSTDS": "passing_tds",
    "KXNFLPASSINT": "passing_interceptions",
    "KXNFLTD": TD_STAT,
    "KXNFLANYTD": TD_STAT,
    "KXNFL2TD": TD_STAT,
}
_FIXED_THRESHOLD = {"KXNFLANYTD": 1, "KXNFL2TD": 2}

# Fantasy points (Sleeper scoring), 2026 only. Not Prop Markets.
FANTASY_LINE_SERIES = "KXNFLFFPTS"  # one binary "points strictly greater than X" market per player-game
FANTASY_MEAN_SERIES = "KXNFLFFPTSLADDER"  # one scalar market: pays $0.01 a point, capped at $1

SERIES: tuple[str, ...] = (*LADDER_SERIES, FANTASY_LINE_SERIES, FANTASY_MEAN_SERIES)

_MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
_TICKER = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<yy>\d\d)(?P<mon>[A-Z]{3})(?P<dd>\d\d)(?P<teams>[A-Z]+)"
                     r"(?:-(?P<player>[A-Z0-9]+)(?:-(?P<suffix>[A-Z0-9]+))?)?$")


@dataclass(frozen=True)
class Ticker:
    """A Kalshi market (or event) ticker, e.g. KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-90."""

    series: str  # "KXNFLRECYDS"
    event: str  # "KXNFLRECYDS-26NOV15PITCIN": one Game, every player's Rungs
    game_date: date  # the Game's US Eastern date (SNF on Nov 15 kicks off 01:20Z Nov 16)
    teams: str  # "PITCIN": both team codes run together, usually away then home
    player: str | None  # "CINJCHASE1": team, first initial, last name, jersey
    suffix: str | None  # "90" (90+), "7P4" (over 7.4), or None


def parse_ticker(ticker: str) -> Ticker:
    """The parts of a Kalshi NFL market or event ticker. Raises ValueError if it names no Game."""
    m = _TICKER.match(ticker)
    if m is None or m["mon"] not in _MONTHS:
        raise ValueError(f"not a Kalshi NFL game ticker: {ticker!r}")
    game_date = date(2000 + int(m["yy"]), _MONTHS[m["mon"]], int(m["dd"]))
    event = f"{m['series']}-{m['yy']}{m['mon']}{m['dd']}{m['teams']}"
    return Ticker(m["series"], event, game_date, m["teams"], m["player"], m["suffix"])


def rung_threshold(market: Mapping) -> int:
    """The Rung's threshold k: the market pays when Y >= k.

    `market` is a stored market record ("ticker", "floor" = Kalshi's floor_strike). Kalshi
    writes "90+" as floor_strike 89.5 and ticker suffix -90; floor_strike is used when present
    because some early 2025 tickers fuse the threshold to the jersey (JACBSTRANGE8520).
    KXNFLANYTD and KXNFL2TD carry no strike: they are the 1+ and 2+ Rungs.
    """
    t = parse_ticker(market["ticker"])
    if t.series in _FIXED_THRESHOLD:
        return _FIXED_THRESHOLD[t.series]
    if market.get("floor") is not None:
        return round(float(market["floor"]) + 0.5)
    if t.suffix is None or not t.suffix.isdigit():
        raise ValueError(f"no threshold in {market['ticker']!r}")
    return int(t.suffix)


def fantasy_line(market: Mapping) -> float:
    """X in a KXNFLFFPTS market "records above X fantasy points" (suffix 7P4 = 7.4)."""
    if market.get("floor") is not None:
        return float(market["floor"])
    suffix = parse_ticker(market["ticker"]).suffix
    if suffix is None:
        raise ValueError(f"no line in {market['ticker']!r}")
    return float(suffix.replace("P", "."))
