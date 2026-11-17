"""Kalshi's settled NFL player-prop markets → Backtest inputs (PLAN S5.1).

Market semantics, the price rule and the drop rules are from research/kalshi.md.
"""

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..markets import PropMarket, Rung, parse_utc
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


def _event_matcher(games: Iterable[ScheduledGame]) -> Callable[[str], ScheduledGame | None]:
    """match_game against a fixed schedule, one lookup per event."""
    games = list(games)
    seen: dict[str, ScheduledGame | None] = {}

    def game_of(ticker: str) -> ScheduledGame | None:
        event = parse_ticker(ticker).event
        if event not in seen:
            seen[event] = match_game(event, games)
        return seen[event]

    return game_of


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


# --- the cache -------------------------------------------------------------------------

DEFAULT_ROOT = Path("data") / "kalshi"  # git-ignored


def compact_market(raw: Mapping) -> dict:
    """The fields of a Kalshi market listing that the Backtest needs (listing pages also
    carry the rules text, which is most of their size)."""
    return {
        "ticker": raw["ticker"],
        "player": (raw.get("custom_strike") or {}).get("football_player"),
        "floor": raw.get("floor_strike"),
        "open": raw.get("open_time"),
        "settled": raw.get("settlement_ts"),
        "result": raw.get("result"),
        "value": raw.get("expiration_value"),  # the stat on scalar markets; "Cancelled" on a DNP
    }


class Cache:
    """The files under `root` (default data/kalshi/):

    markets/<SERIES>.json  {ticker: compact market}, merged across pulls
    players.json           {Kalshi player uuid: {name, position, source_id, gsis_id}}
    games.json             the nflverse Games of the pulled seasons, with kickoffs in UTC
    quotes.jsonl           one kickoff quote per line, appended as candles arrive:
                           {ticker, bid, ask, end, volume, interval (1 or 60 minutes; None: no candle)}
    """

    def __init__(self, root: str | Path = DEFAULT_ROOT):
        self.root = Path(root)

    def _read(self, name: str, default):
        path = self.root / name
        return json.loads(path.read_text()) if path.exists() else default

    def _write(self, name: str, data) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")  # write aside, then rename: never half a file
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(path)

    def markets(self, series: str) -> dict[str, dict]:
        return self._read(f"markets/{series}.json", {})

    def save_markets(self, series: str, markets: Iterable[Mapping]) -> None:
        """Merge `markets` (compact records) into the series' file."""
        held = self.markets(series)
        held.update({m["ticker"]: dict(m) for m in markets})
        self._write(f"markets/{series}.json", dict(sorted(held.items())))

    def players(self) -> dict[str, dict]:
        return self._read("players.json", {})

    def save_players(self, players: Mapping[str, Mapping]) -> None:
        self._write("players.json", dict(sorted(players.items())))

    def games(self) -> list[ScheduledGame]:
        return [ScheduledGame(**g) for g in self._read("games.json", [])]

    def save_games(self, games: Iterable[ScheduledGame]) -> None:
        """Merge `games` into the stored schedule (by game_id)."""
        held = {g.game_id: g for g in self.games()}
        held.update({g.game_id: g for g in games})
        self._write("games.json", [asdict(g) for g in sorted(held.values(), key=lambda g: g.kickoff)])

    def quotes(self) -> dict[str, dict]:
        """{ticker: its kickoff quote record}. A line cut short by a crash is skipped."""
        path = self.root / "quotes.jsonl"
        out = {}
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    q = json.loads(line)
                except ValueError:
                    continue
                out[q["ticker"]] = q
        return out

    def append_quote(self, quote: Mapping) -> None:
        path = self.root / "quotes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(quote, separators=(",", ":")) + "\n")


# --- Backtest inputs -------------------------------------------------------------------


@dataclass(frozen=True)
class KalshiRung:
    """One Kalshi Rung at kickoff: P(stat >= threshold) is its bid/ask mid (an exchange has no vig)."""

    game_id: str  # nflverse game_id, e.g. "2026_10_PIT_CIN"
    season: int
    week: int
    kickoff: str  # ISO 8601 UTC, e.g. "2026-11-16T01:20:00Z"
    gsis_id: str
    player: str  # name as Kalshi spells it
    stat: str  # Scoring Stat or Usage Stat key, e.g. "receiving_yards", "rush_rec_tds"
    threshold: int  # the Rung pays when Y >= threshold
    bid: float  # dollars, at the last candle ending at or before kickoff
    ask: float

    @property
    def mid(self) -> float:
        """The Fair Probability."""
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class KalshiFantasyLine:
    """A KXNFLFFPTS market at kickoff: P(fantasy points > line) is its mid. Sleeper scoring."""

    game_id: str
    season: int
    week: int
    kickoff: str
    gsis_id: str
    player: str
    line: float  # pays when points > line, strictly
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class KalshiFantasyMean:
    """A KXNFLFFPTSLADDER market at kickoff: a scalar paying $0.01 a fantasy point, capped at $1,
    so its mid is the market's expected (capped) points / 100. Sleeper scoring."""

    game_id: str
    season: int
    week: int
    kickoff: str
    gsis_id: str
    player: str
    price: float  # the mid: expected points / 100, with points capped at 100
    bid: float
    ask: float

    @property
    def expected_points(self) -> float:
        return 100 * self.price


def load_rungs(root: str | Path = DEFAULT_ROOT) -> list[KalshiRung]:
    """Every usable Kalshi Rung in the cache, by kickoff, game, player, stat and threshold.

    Reads only the cache (no network). The drop rules are in `drop_counts`.
    """
    return _load(root)[0]


def load_fantasy_lines(root: str | Path = DEFAULT_ROOT) -> list[KalshiFantasyLine]:
    """Every usable KXNFLFFPTS line in the cache (cache only)."""
    return _load(root)[1]


def load_fantasy_means(root: str | Path = DEFAULT_ROOT) -> list[KalshiFantasyMean]:
    """Every usable KXNFLFFPTSLADDER price in the cache (cache only)."""
    return _load(root)[2]


def drop_counts(root: str | Path = DEFAULT_ROOT) -> Counter:
    """{(series, reason): markets} left out of the loads, checked in this order:

    no_game          the event matches no Game in games.json (preseason, or a season not pulled)
    opened_in_game   listed at or after kickoff
    dnp              settled at the pre-game fair price: the player didn't play (result "scalar";
                     on KXNFLFFPTSLADDER, which always settles "scalar": no numeric value)
    unmapped_player  the player uuid has no gsis_id (D/ST, "No TD", or no nflverse match)
    not_fetched      no candles pulled yet
    no_candle        no candle ended at or before kickoff, minute or hourly
    one_sided        no bid, or no ask below $1
    wide_spread      ask − bid above MAX_SPREAD
    """
    return _load(root)[3]


def _market_drop_reason(series: str, market: Mapping, game: ScheduledGame | None,
                        players: Mapping[str, Mapping]) -> str | None:
    """Why a market is dropped before its price is looked at, or None."""
    if game is None:
        return "no_game"
    if market.get("open") and parse_utc(market["open"]) >= parse_utc(game.kickoff):
        return "opened_in_game"
    if series == FANTASY_MEAN_SERIES:
        if not _is_number(market.get("value")):
            return "dnp"
    elif market.get("result") == "scalar":
        return "dnp"
    if not (players.get(market.get("player")) or {}).get("gsis_id"):
        return "unmapped_player"
    return None


def _is_number(value) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _load(root):
    cache = Cache(root)
    games, players, quotes = cache.games(), cache.players(), cache.quotes()
    rungs, lines, means, drops = [], [], [], Counter()
    game_of = _event_matcher(games)
    for series in SERIES:
        for market in cache.markets(series).values():
            game = game_of(market["ticker"])
            reason = _market_drop_reason(series, market, game, players)
            q = quotes.get(market["ticker"])
            if reason is None:
                if q is None:
                    reason = "not_fetched"
                elif q.get("interval") is None:
                    reason = "no_candle"
                else:
                    reason = price_drop_reason(Quote(q["bid"], q["ask"], q["end"], q["volume"]))
            if reason is not None:
                drops[(series, reason)] += 1
                continue
            player = players[market["player"]]
            head = (game.game_id, game.season, game.week, game.kickoff, player["gsis_id"], player["name"])
            if series == FANTASY_LINE_SERIES:
                lines.append(KalshiFantasyLine(*head, fantasy_line(market), q["bid"], q["ask"]))
            elif series == FANTASY_MEAN_SERIES:
                means.append(KalshiFantasyMean(*head, (q["bid"] + q["ask"]) / 2, q["bid"], q["ask"]))
            else:
                rungs.append(KalshiRung(*head, LADDER_SERIES[series], rung_threshold(market), q["bid"], q["ask"]))
    order = lambda r: (r.kickoff, r.game_id, r.player, r.gsis_id)  # noqa: E731
    rungs.sort(key=lambda r: (*order(r), r.stat, r.threshold))
    lines.sort(key=lambda r: (*order(r), r.line))
    means.sort(key=order)
    return rungs, lines, means, drops


def _american(p: float) -> float:
    """American odds whose implied probability is p, unrounded."""
    return -100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def to_prop_markets(rungs: Iterable[KalshiRung]) -> list[PropMarket]:
    """One Kalshi Prop Market per (Game, player, stat): no Main Line, a Ladder of every Rung with
    the American odds of its mid. With no vig, fair_points(markets, prior_c={"kalshi":
    fair.KALSHI_PRIOR_C}) gives back the mids (after isotonic, and clipped to PROB_FLOOR).

    PropMarket.player is Kalshi's spelling; the gsis_id is on the Rungs.
    """
    groups: dict[tuple, list[KalshiRung]] = {}
    for r in rungs:
        groups.setdefault((r.game_id, r.gsis_id, r.stat), []).append(r)
    return [
        PropMarket(
            player=rs[0].player, stat=stat, game_id=game_id, book=BOOK, main_line=None,
            ladder=tuple(Rung(r.threshold, _american(r.mid), BOOK) for r in sorted(rs, key=lambda r: r.threshold)),
        )
        for (game_id, _, stat), rs in groups.items()
    ]
