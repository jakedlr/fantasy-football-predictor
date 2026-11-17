"""Kalshi's settled NFL player-prop markets → Backtest inputs (PLAN S5.1).

Market semantics, the price rule and the drop rules are from research/kalshi.md.

Reading (cache only, no network):
    load_rungs(root) -> [KalshiRung]                 Ladder Rungs, P(Y >= threshold) = mid
    load_fantasy_lines(root) -> [KalshiFantasyLine]  KXNFLFFPTS, P(points > line) = mid
    load_fantasy_means(root) -> [KalshiFantasyMean]  KXNFLFFPTSLADDER, mid = expected points / 100
    drop_counts(root) -> Counter{(series, reason)}   what was left out, and why
    to_prop_markets(rungs) -> [PropMarket]           book "kalshi"; fair_points(markets,
                                                     prior_c={"kalshi": fair.KALSHI_PRIOR_C}) = the mids
Fetching:
    pull(root, seasons, http=None, since=None, ...)  resumable, ≤ 4 requests/s
    python -m ffp.sources.kalshi pull --root data/kalshi --since 2025-10-01

Which stats exist when: receiving_yards, rushing_yards, passing_yards from 2025 Week 5;
receptions from 2025 Week 8; passing_tds from 2025 Week 12; carries and passing_interceptions
from 2026 Week 1 only. rush_rec_tds: 1+ and 2+ Rungs in 2025 (KXNFLANYTD, KXNFL2TD), 1+/2+/3+ in
2026 (KXNFLTD). Fantasy points lines from 2026 Week 1, means from 2026 Week 2.
"""

import argparse
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..markets import PropMarket, Rung, parse_utc
from ..players import ScheduledGame, UnknownTeamError, _present, load_schedule, team_code_from_book_name
from .fanduel import TD_STAT
from .http import FetchError

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
        if details.get("league") != "NFL" or t.get("type", "football_player") != "football_player":
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
    listing.json           how far each series has been listed, so a rerun lists only what's new
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


# --- the network -----------------------------------------------------------------------

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
RATE = 4.0  # requests a second at most; research/kalshi.md saw no 429 at this pace
PAGE = 1000  # markets per listing page (Kalshi's maximum)
BATCH = 100  # tickers per live candlesticks request (Kalshi's maximum)
MINUTE_WINDOW = 3600  # 1-minute candles over the hour before kickoff
HOUR_WINDOW = 24 * 3600  # the fallback: hourly candles over the day before
RELIST_OVERLAP = 7 * 24 * 3600  # a live listing re-reads the last week before the previous listing
PROGRESS_EVERY = 500  # kickoff quotes between progress log lines

log = logging.getLogger(__name__)

Fetch = Callable[[str], tuple]  # url -> (status, body, headers), like ffp.sources.http


class NotFound(FetchError):
    """HTTP 404."""


def urllib_fetch(url: str) -> tuple[int, bytes, dict]:
    """A plain GET: Kalshi's public market data needs no key and no browser impersonation."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ffp/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def api_url(path: str, **params) -> str:
    """BASE_URL + path, with params (those not None) in the order given."""
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return BASE_URL + path + ("?" + query if query else "")


class KalshiHttp:
    """GETs JSON from Kalshi's public API, at most `rate` requests a second. A 429 is waited out
    (1, 2, 4, ... s: Kalshi sends no Retry-After) and retried. Nothing raw is written to disk:
    `pull` keeps only compact records. `fetch` is injected so tests never touch the network."""

    def __init__(self, fetch: Fetch = urllib_fetch, rate: float = RATE, retries: int = 5,
                 sleep=time.sleep, clock=time.monotonic):
        self.fetch, self.gap, self.retries, self.sleep, self.clock = fetch, 1 / rate, retries, sleep, clock
        self.requests = 0
        self._next = float("-inf")

    def get_json(self, url: str):
        for attempt in range(self.retries + 1):
            wait = self._next - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._next = self.clock() + self.gap
            self.requests += 1
            status, body, *_ = self.fetch(url)
            if status == 429 and attempt < self.retries:
                log.warning("HTTP 429 from %s: waiting %ds", url, 2 ** attempt)
                self.sleep(2 ** attempt)
                continue
            if status == 404:
                raise NotFound(f"HTTP 404 from {url}")
            if status != 200:
                raise FetchError(f"HTTP {status} from {url}")
            try:
                return json.loads(body)
            except ValueError:
                raise FetchError(f"non-JSON response from {url}") from None
        raise AssertionError("unreachable")


def _paged(http, path: str, key: str, **params) -> Iterator[dict]:
    cursor = None
    while True:
        page = http.get_json(api_url(path, **params, cursor=cursor))
        yield from page.get(key) or []
        cursor = page.get("cursor")
        if not cursor or not page.get(key):
            return


def _ts(moment: str) -> int:
    return int(parse_utc(moment).timestamp())


@dataclass(frozen=True)
class PullReport:
    """What one `pull` did, and what the cache now holds."""

    requests: int
    listed: dict[str, int]  # {series: markets listed in this pull}
    quotes_fetched: int  # markets whose kickoff quote this pull looked up
    rungs: int  # usable, in the whole cache
    fantasy_lines: int
    fantasy_means: int
    drops: Counter  # drop_counts of the whole cache


def pull(root: str | Path = DEFAULT_ROOT, seasons: Iterable[int] = (2025, 2026), http=None,
         since: date | None = None, series: Iterable[str] = SERIES,
         schedule_fetch: Callable | None = None, roster_fetch: RosterFetch | None = None) -> PullReport:
    """Bring the cache under `root` up to date from Kalshi's API. Resumable: run it again after an
    interruption, or each week, and only what's missing is fetched.

    1. The nflverse schedule of `seasons` (kickoffs) → games.json.
    2. Every settled market of `series`, from /historical/markets (settled before
       /historical/cutoff; listed once per cutoff) and /markets?status=settled (incremental) →
       markets/<SERIES>.json, compacted. Markets of Games before `since` are skipped.
    3. The football_player directory and nflverse rosters → players.json, when a market names a
       player not in it yet.
    4. The kickoff quote of every market that survives the drop rules and has none yet →
       quotes.jsonl: 1-minute candles over the hour before kickoff, else hourly over the day
       before; one request per historical market, ≤ 100 live markets per request.

    `http` is anything with get_json(url) (default KalshiHttp()). `schedule_fetch(season)` and
    `roster_fetch(season)` default to nflreadpy, as in ffp.players.
    """
    cache, http, seasons, series = Cache(root), http or KalshiHttp(), sorted(set(seasons)), list(series)
    unknown = [s for s in series if s not in SERIES]
    if unknown:
        raise ValueError(f"unknown Kalshi series {', '.join(unknown)} (known: {', '.join(SERIES)})")
    start = getattr(http, "requests", 0)

    games = [g for s in seasons for g in load_schedule(s, schedule_fetch)]
    cache.save_games(games)
    cutoff = parse_utc(http.get_json(api_url("/historical/cutoff"))["market_settled_ts"])
    listed = {s: _list_markets(http, cache, s, cutoff, since) for s in series}
    _update_players(http, cache, series, seasons, roster_fetch)
    fetched = _fetch_quotes(http, cache, series, cutoff, since, {g.game_id for g in games})

    rungs, lines, means, drops = _load(root)
    return PullReport(getattr(http, "requests", 0) - start, listed, fetched, len(rungs), len(lines), len(means),
                      drops)


def _list_markets(http, cache: Cache, series: str, cutoff: datetime, since: date | None) -> int:
    """List the series' settled markets into the cache; the number listed.

    The historical listing changes only when the cutoff moves, so it is read once per cutoff
    (listing.json remembers it, and from which `since`). The live listing is read each pull
    from `since`, or from a week before the previous live listing.
    """
    state = cache._read("listing.json", {})
    mine = state.setdefault(series, {})
    wanted = since and since.isoformat()
    since_ts = int(datetime.combine(since, datetime.min.time(), UTC).timestamp()) if since else None
    sources = []

    hist = mine.get("historical")  # {"cutoff", "since"} of the last historical listing
    if not (hist and hist["cutoff"] == cutoff.isoformat() and _covers(hist["since"], wanted)) and (
            since is None or since < cutoff.date()):
        sources.append(("historical", _paged(http, "/historical/markets", "markets", series_ticker=series,
                                             limit=PAGE)))
    live = mine.get("live")  # {"at", "since"}: listed at `at`, back to `since`
    live_since = wanted
    if live and _covers(live["since"], wanted):
        since_ts, live_since = max(since_ts or 0, live["at"] - RELIST_OVERLAP), live["since"]
    listed_at = int(time.time())
    sources.append(("live", _paged(http, "/markets", "markets", series_ticker=series, status="settled",
                                   limit=PAGE, min_close_ts=since_ts)))
    n = 0
    for name, markets in sources:
        keep = [compact_market(m) for m in markets
                if since is None or parse_ticker(m["ticker"]).game_date >= since]
        cache.save_markets(series, keep)
        n += len(keep)
        if name == "historical":
            mine["historical"] = {"cutoff": cutoff.isoformat(), "since": wanted}
    mine["live"] = {"at": listed_at, "since": live_since}
    cache._write("listing.json", state)
    log.info("%s: %d markets listed", series, n)
    return n


def _covers(held: str | None, wanted: str | None) -> bool:
    """Whether a listing back to `held` (None: everything) covers one back to `wanted`."""
    return held is None or (wanted is not None and wanted >= held)


def _update_players(http, cache: Cache, series: list[str], seasons: list[int], roster_fetch) -> None:
    players = cache.players()
    wanted = {m["player"] for s in series for m in cache.markets(s).values() if m.get("player")}
    if wanted - players.keys():
        directory = player_directory(_paged(http, "/structured_targets", "structured_targets",
                                            type="football_player", page_size=2000))
        players.update({u: {**p, "gsis_id": None} for u, p in directory.items() if u not in players})
    for uuid in sorted(wanted - players.keys()):  # D/ST, "No TD", or not in the directory listing
        try:
            target = http.get_json(api_url(f"/structured_targets/{uuid}")).get("structured_target") or {}
        except NotFound:
            target = {}
        found = player_directory([target]) if target.get("id") else {}
        players[uuid] = {**found.get(uuid, {"name": target.get("name"), "position": None, "source_id": None}),
                         "gsis_id": None}
    if any(p.get("source_id") and not p.get("gsis_id") for p in players.values()):
        to_gsis = sportradar_to_gsis(seasons, roster_fetch)
        for p in players.values():
            if p.get("source_id") and not p.get("gsis_id"):
                p["gsis_id"] = to_gsis.get(p["source_id"])
    cache.save_players(players)


def _fetch_quotes(http, cache: Cache, series: list[str], cutoff: datetime, since: date | None,
                  game_ids: set[str]) -> int:
    """Kickoff quotes for every market that passes the pre-price drop rules and has none yet."""
    players, have = cache.players(), cache.quotes().keys()
    game_of = _event_matcher(cache.games())
    historical, live = [], {}
    for s in series:
        for m in cache.markets(s).values():
            game = game_of(m["ticker"])
            if (m["ticker"] in have or game is None or game.game_id not in game_ids
                    or (since and _eastern_date(game.kickoff) < since)
                    or _market_drop_reason(s, m, game, players) is not None):
                continue
            ko = _ts(game.kickoff)
            if m.get("settled") and parse_utc(m["settled"]) < cutoff:
                historical.append((m["ticker"], ko))
            else:  # any series of any Game kicking off together shares a batch: same window
                live.setdefault(ko, []).append(m["ticker"])
    total = len(historical) + sum(map(len, live.values()))
    log.info("kickoff quotes to fetch: %d historical, %d live", len(historical), total - len(historical))
    done = 0

    def progress(n):
        nonlocal done
        if (done + n) // PROGRESS_EVERY > done // PROGRESS_EVERY or done + n == total:
            log.info("kickoff quotes: %d / %d", done + n, total)
        done += n

    for ticker, ko in historical:
        def candles(interval, window, _chunk, ticker=ticker, ko=ko):  # one market per request
            page = http.get_json(api_url(f"/historical/markets/{ticker}/candlesticks", start_ts=ko - window,
                                         end_ts=ko, period_interval=interval))
            return {ticker: page.get("candlesticks") or []}
        try:
            _quote_batch(cache, [ticker], ko, candles)
        except NotFound:
            log.warning("no historical candles for %s; tried again next pull", ticker)
        progress(1)
    for ko, tickers in live.items():
        def candles(interval, window, chunk, ko=ko):
            page = http.get_json(api_url("/markets/candlesticks", market_tickers=",".join(chunk),
                                         start_ts=ko - window, end_ts=ko, period_interval=interval))
            return {x.get("market_ticker"): x.get("candlesticks") or [] for x in page.get("markets") or []}
        for i in range(0, len(tickers), BATCH):
            _quote_batch(cache, tickers[i:i + BATCH], ko, candles)
            progress(len(tickers[i:i + BATCH]))
    return total


def _quote_batch(cache: Cache, tickers: list[str], ko: int, candles) -> None:
    """Append each ticker's kickoff quote: minute candles first, then hourly for those with none.

    `candles(interval, window, tickers)` returns {ticker: raw candles}.
    """
    missing = tickers
    for interval, window in ((1, MINUTE_WINDOW), (60, HOUR_WINDOW)):
        got = candles(interval, window, missing)
        still = []
        for t in missing:
            quote = kickoff_quote(got.get(t, []), ko)
            if quote:
                cache.append_quote(_quote_record(t, quote, interval))
            else:
                still.append(t)
        missing = still
        if not missing:
            return
    for t in missing:
        cache.append_quote(_quote_record(t, None, None))


def _quote_record(ticker: str, quote: Quote | None, interval: int | None) -> dict:
    if quote is None:
        return {"ticker": ticker, "bid": None, "ask": None, "end": None, "volume": 0.0, "interval": None}
    return {"ticker": ticker, "bid": quote.bid, "ask": quote.ask, "end": quote.end, "volume": quote.volume,
            "interval": interval}


# --- CLI -------------------------------------------------------------------------------


def _season_of(day: date) -> int:
    """The NFL season a date falls in: Mar–Dec of year Y and Jan–Feb of Y+1 are season Y."""
    return day.year if day.month >= 3 else day.year - 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ffp.sources.kalshi",
                                     description="Kalshi's settled NFL player props → the Backtest cache.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pull", help="fetch what's missing from the cache, then print counts and drop reasons")
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT, help=f"cache directory (default {DEFAULT_ROOT})")
    p.add_argument("--since", type=date.fromisoformat, help="skip Games before this date, e.g. 2025-10-01")
    p.add_argument("--seasons", type=int, nargs="+",
                   help="nflverse seasons to join (default: --since's season, or 2025, through this season)")
    p.add_argument("--series", nargs="+", default=list(SERIES), help="Kalshi series (default: all used)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    first = _season_of(args.since) if args.since else 2025
    seasons = args.seasons or list(range(first, _season_of(date.today()) + 1))
    report = pull(args.root, seasons, since=args.since, series=args.series)
    print(format_report(report))
    return 0


def format_report(report: PullReport) -> str:
    lines = [f"requests: {report.requests}   kickoff quotes fetched: {report.quotes_fetched}",
             f"usable: {report.rungs} Rungs, {report.fantasy_lines} fantasy lines, "
             f"{report.fantasy_means} fantasy means", "", "listed this pull:"]
    lines += [f"  {s:<18} {n}" for s, n in report.listed.items()]
    lines += ["", "dropped (whole cache):"]
    lines += [f"  {s:<18} {reason:<16} {n}" for (s, reason), n in sorted(report.drops.items())]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
