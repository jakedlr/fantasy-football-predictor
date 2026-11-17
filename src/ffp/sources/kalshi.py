"""Kalshi's settled NFL player-prop markets → Backtest inputs (PLAN S5.1).

Market semantics, the price rule and the drop rules are from research/kalshi.md.
"""

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..players import ScheduledGame, UnknownTeamError, _present, team_code_from_book_name
from .fanduel import TD_STAT

_EASTERN = ZoneInfo("America/New_York")

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


# --- the price rule --------------------------------------------------------------------

MAX_SPREAD = 0.10  # dollars; a wider book is dropped
_CENT = 1e-9  # float slack when comparing dollar prices


@dataclass(frozen=True)
class Quote:
    """The closing yes bid and ask of one candle, in dollars. None: that side had no order."""

    bid: float | None
    ask: float | None
    end: int  # the candle's end_period_ts (Unix seconds)
    volume: float  # contracts traded in the candle


def _dollars(side: Mapping | None) -> float | None:
    side = side or {}
    value = side.get("close_dollars", side.get("close"))
    return None if value in (None, "") else float(value)


def kickoff_quote(candles: list[Mapping], kickoff_ts: int) -> Quote | None:
    """The last candle ending at or before kickoff, or None if there is none.

    Trading runs on in-game, so every later candle is cut. Reads both the live
    (close_dollars, volume_fp) and the historical (close, volume) field names.
    """
    before = [c for c in candles if c["end_period_ts"] <= kickoff_ts]
    if not before:
        return None
    c = max(before, key=lambda c: c["end_period_ts"])
    volume = c.get("volume_fp", c.get("volume"))
    return Quote(_dollars(c.get("yes_bid")), _dollars(c.get("yes_ask")), int(c["end_period_ts"]),
                 float(volume) if volume not in (None, "") else 0.0)


def price_drop_reason(quote: Quote) -> str | None:
    """Why a Rung's kickoff quote can't give a Fair Probability, or None if it can.

    "one_sided": no bid (or a 0 bid) or no ask below $1, nearly always a far-tail Rung.
    "wide_spread": ask − bid above MAX_SPREAD. Otherwise the Fair Probability is the mid.
    """
    if not quote.bid or quote.ask is None or quote.ask >= 1 - _CENT:
        return "one_sided"
    if quote.ask - quote.bid > MAX_SPREAD + _CENT:
        return "wide_spread"
    return None


# --- the kickoff join ------------------------------------------------------------------


def _team_codes(letters: str) -> list[frozenset[str]]:
    """Every way `letters` ("PITCIN", "KCJAC", "LARSEA") splits into two nflverse team codes."""
    splits = []
    for i in range(2, len(letters) - 1):
        try:
            a, b = team_code_from_book_name(letters[:i]), team_code_from_book_name(letters[i:])
        except UnknownTeamError:
            continue
        splits.append(frozenset((a, b)))
    return splits


def _eastern_date(kickoff: str) -> date:
    return datetime.fromisoformat(kickoff.replace("Z", "+00:00")).astimezone(_EASTERN).date()


def match_game(ticker: str, games: Iterable[ScheduledGame]) -> ScheduledGame | None:
    """The nflverse Game a Kalshi event or market ticker is on, or None (preseason, other seasons).

    Joined on the event's Eastern date and the set of its two teams: Kalshi runs the
    codes together, mostly away then home, and spells LA as LAR and JAX as JAC.
    """
    t = parse_ticker(ticker)
    pairs = _team_codes(t.teams)
    for game in games:
        if frozenset((game.home, game.away)) in pairs and _eastern_date(game.kickoff) == t.game_date:
            return game
    return None


# --- players ---------------------------------------------------------------------------


def player_directory(targets: Iterable[Mapping]) -> dict[str, dict]:
    """Kalshi football_player structured targets → {uuid: {name, position, source_id}}, NFL only.

    `source_id` is the nflverse sportradar_id (research/kalshi.md §1). The directory also
    lists college players; they are left out.
    """
    out = {}
    for t in targets:
        details = t.get("details") or {}
        if details.get("league") != "NFL":
            continue
        source_id = t.get("source_id") or (t.get("source_ids") or {}).get("source_3_id")
        out[t["id"]] = {"name": t.get("name"), "position": details.get("position"), "source_id": source_id}
    return out


RosterFetch = Callable[[int], Iterable[Mapping]]


def sportradar_to_gsis(seasons: Iterable[int], fetch: RosterFetch | None = None) -> dict[str, str]:
    """{sportradar_id: gsis_id} from nflverse rosters of `seasons`.

    `fetch(season)` returns nflverse `load_rosters` rows; it defaults to nflreadpy
    (network, cached by nflreadpy), like players.load_roster.
    """
    out = {}
    for season in seasons:
        for row in (fetch or _nflreadpy_rosters)(season):
            sid, gsis = row.get("sportradar_id"), row.get("gsis_id")
            if _present(sid) and _present(gsis):
                out[str(sid)] = str(gsis)
    return out


def _nflreadpy_rosters(season: int) -> list[dict]:
    import nflreadpy  # imported here so tests and offline runs never need it

    return nflreadpy.load_rosters(season).to_dicts()
