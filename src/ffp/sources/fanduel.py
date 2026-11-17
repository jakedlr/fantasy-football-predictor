"""FanDuel sbapi responses → Games and Prop Markets.

Pure functions over already-fetched JSON. The league page and each event-page
tab keep Games at `attachments.events{}` and markets at
`attachments.markets{}`; each market carries its own `runners[]`. See
research/sportsbook-markets.md, FanDuel section.
"""

import re
from collections.abc import Iterator
from datetime import datetime

from ..markets import (
    DST_TDS,
    TEAM_POINTS,
    Game,
    GameLine,
    MainLine,
    PropMarket,
    Rung,
    ladder_without_main_line_copy,
    parse_utc,
)

BOOK = "fanduel"

# Scoring Stat → the stem of FD's market types for it. "PLAYER_X_<stem>_<tier>" is the
# Main Line, "PLAYER_X_ALT_<stem>_<tier>" the Ladder. The stem is what tells rushing from
# passing yards: both Ladders' runners read "<Player> N+ Yards".
MARKET_STEMS = {
    "receiving_yards": "RECEIVING_YARDS",
    "receptions": "RECEPTIONS",
    "passing_yards": "PASSING_YARDS",
    "passing_tds": "PASSING_TOUCHDOWNS",
    "rushing_yards": "RUSHING_YARDS",
}

# Stats FanDuel posts no market for (research/sportsbook-markets.md, Sprint 4 re-check):
# no interceptions thrown, and no rush attempts (carries, a Usage Stat). They have no tab;
# parse_prop_markets gives no Prop Markets for them rather than an error.
NOT_OFFERED = frozenset({"passing_interceptions", "carries"})

# Rushing + receiving TDs, split later (not a Scoring Stat): only one-sided scorer markets,
# one per Game, each runner a bare player name. Market type → the Rung's threshold N
# ("N or more TDs"). First, last and per-half scorer markets are not "N or more": ignored.
TD_STAT = "rush_rec_tds"
TD_MARKET_THRESHOLDS = {"ANY_TIME_TOUCHDOWN_SCORER": 1, "TO_SCORE_2+_TOUCHDOWNS": 2, "TO_SCORE_3+_TOUCHDOWNS": 3}

# Scoring Stat → the event-page tab slug (the `tab=` query value) that holds its markets.
TABS = {
    "receiving_yards": "receiving-props",
    "receptions": "receiving-props",
    "passing_yards": "passing-props",
    "passing_tds": "passing-props",
    "rushing_yards": "rushing-props",
    TD_STAT: "td-scorer-props",
}

# (player name as FD spells it, FD event id)
Key = tuple[str, str]

_OVER_UNDER = re.compile(r"^(?P<player>.+) (?P<side>Over|Under)$")
# "Zay Flowers 70+ Yards", "Zay Flowers 4+ Receptions": the threshold is only in the name.
_RUNG = re.compile(r"^(?P<player>.+) (?P<threshold>\d+)\+(?: .*)?$")


def parse_games(page_json: dict, now: datetime | None = None) -> list[Game]:
    """The Games listed in a FanDuel league page or event-page tab.

    Futures and specials (no " @ " in the name) are not Games. A Game is
    started when FD flags it inPlay or, given `now`, once its kickoff has
    passed: the league page's events carry no inPlay flag.
    """
    games = []
    for event in page_json.get("attachments", {}).get("events", {}).values():
        teams = event.get("name", "").split(" @ ")
        if len(teams) != 2:
            continue
        away, home = teams
        kickoff = parse_utc(event["openDate"])
        games.append(
            Game(
                game_id=str(event["eventId"]),
                away=away,
                home=home,
                kickoff=kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                book=BOOK,
                started=bool(event.get("inPlay")) or (now is not None and kickoff <= now),
            )
        )
    return games


def _markets(tab_json: dict) -> list[dict]:
    return list(tab_json.get("attachments", {}).get("markets", {}).values())


def props_posted(event_tab_json: dict) -> bool:
    """Whether an event-page tab holds any player markets yet.

    Days before a Game, FD's prop tabs answer with only the moneyline, spread
    and total: "not posted, rerun closer to kickoff", not an error.
    """
    return any(
        runner.get("isPlayerSelection")
        for market in _markets(event_tab_json)
        for runner in market.get("runners", [])
    )


def market_key(market_type: str) -> str:
    """PLAYER_X_ALT_RECEIVING_YARDS_LOW → PLAYER_ALT_RECEIVING_YARDS.

    Drops the player letter (X, or A/B in older data) and the tier suffix. The
    LOW/MEDIUM/HIGH tiers are per-player buckets, not alternate lines.
    """
    key = re.sub(r"^PLAYER_[A-Z]_", "PLAYER_", market_type)
    return re.sub(r"_(LOW|MEDIUM|HIGH)$", "", key)


def _active_runners(market: dict) -> list[dict]:
    return [r for r in market.get("runners", []) if r.get("runnerStatus", "ACTIVE") == "ACTIVE"]


def _odds(runner: dict) -> float:
    return float(runner["winRunnerOdds"]["americanDisplayOdds"]["americanOddsInt"])


def _logo_slug(runner: dict) -> str | None:
    """".../nfl/baltimore_ravens_jersey.png" → "baltimore_ravens"."""
    logo = runner.get("logo")
    if not logo:
        return None
    name = logo.rsplit("/", 1)[-1].removesuffix(".png")
    return name.removesuffix("_jersey")


def _slug(team: str) -> str:
    """"Los Angeles Chargers" → "los_angeles_chargers", as FD names its logos."""
    return re.sub(r"[^a-z0-9]+", "_", team.lower()).strip("_")


def _markets_of(markets: list[dict], key: str) -> Iterator[tuple[str, list[dict]]]:
    """(event id, active runners) for each market whose type normalises to `key`."""
    for market in markets:
        if market_key(market.get("marketType", "")) == key:
            yield str(market.get("eventId")), _active_runners(market)


def _ladders(
    markets: list[dict], ladder_key: str, slugs: dict[Key, str | None]
) -> dict[Key, list[Rung]]:
    """Each player's Rungs, merged over tiers: "N+" means Y >= N.

    A threshold seen twice for one player and Game keeps its first price.
    """
    rungs: dict[Key, dict[int, Rung]] = {}
    for event_id, runners in _markets_of(markets, ladder_key):
        for runner in runners:
            match = _RUNG.match(runner.get("runnerName", ""))
            if match is None:
                continue
            key = (match["player"], event_id)
            threshold = int(match["threshold"])
            rungs.setdefault(key, {}).setdefault(threshold, Rung(threshold, _odds(runner), BOOK))
            slugs.setdefault(key, _logo_slug(runner))
    return {key: list(by_threshold.values()) for key, by_threshold in rungs.items()}


def _td_ladders(markets: list[dict], slugs: dict[Key, str | None]) -> dict[Key, list[Rung]]:
    """Each player's TD Rungs from the scorer markets: anytime = 1+, 2+ and 3+.

    Team defenses ("Baltimore Defense") are listed as player selections; they're skipped.
    """
    rungs: dict[Key, dict[int, Rung]] = {}
    for market in markets:
        threshold = TD_MARKET_THRESHOLDS.get(market.get("marketType", ""))
        if threshold is None:
            continue
        event_id = str(market.get("eventId"))
        for runner in _active_runners(market):
            player = runner.get("runnerName", "")
            if not player or player.endswith(" Defense"):
                continue
            key = (player, event_id)
            rungs.setdefault(key, {}).setdefault(threshold, Rung(threshold, _odds(runner), BOOK))
            slugs.setdefault(key, _logo_slug(runner))
    return {key: list(by_threshold.values()) for key, by_threshold in rungs.items()}


def _main_lines(
    markets: list[dict], main_key: str, slugs: dict[Key, str | None]
) -> dict[Key, MainLine]:
    """Each player's Main Line: one player's Over and Under at one line in one market."""
    main_lines: dict[Key, MainLine] = {}
    for event_id, runners in _markets_of(markets, main_key):
        sides: dict[str, tuple[str, float, float, str | None]] = {}
        for runner in runners:
            match = _OVER_UNDER.match(runner.get("runnerName", ""))
            if match is not None:
                sides[match["side"]] = (
                    match["player"],
                    float(runner["handicap"]),
                    _odds(runner),
                    _logo_slug(runner),
                )
        if set(sides) != {"Over", "Under"}:
            continue
        (player, line, over_odds, slug), (under_player, under_line, under_odds, _) = (
            sides["Over"],
            sides["Under"],
        )
        key = (player, event_id)
        if (under_player, under_line) != (player, line) or key in main_lines:
            continue
        main_lines[key] = MainLine(line, over_odds, under_odds, BOOK)
        slugs.setdefault(key, slug)
    return main_lines


def _team(game: Game | None, slug: str | None) -> str | None:
    """The player's team: FD gives only a logo, so match its slug to the Game's teams."""
    if game is None or slug is None:
        return None
    return {_slug(game.home): game.home, _slug(game.away): game.away}.get(slug)



def parse_prop_markets(event_tab_json: dict, stat: str) -> list[PropMarket]:
    """One Prop Market per player per Game, from one FanDuel event-page tab.

    The Main Line and the Ladder come from the same tab. A player with only
    one of them gets main_line None or an empty ladder. Started Games are
    excluded. An empty result may mean the tab is not posted yet; ask
    props_posted() to tell.

    For TD_STAT the tab is the TD scorer tab: no Main Line, and the anytime,
    2+ and 3+ scorer prices are Rungs 1, 2 and 3.

    A stat in NOT_OFFERED gives no Prop Markets from any tab; any other stat
    without a tab is a ValueError.
    """
    if stat in NOT_OFFERED:
        return []
    if stat not in TABS:
        raise ValueError(f"no FanDuel market for Scoring Stat {stat!r}")
    markets = _markets(event_tab_json)
    games = {g.game_id: g for g in parse_games(event_tab_json)}
    slugs: dict[Key, str | None] = {}
    if stat == TD_STAT:
        ladders, main_lines = _td_ladders(markets, slugs), {}
    else:
        stem = MARKET_STEMS[stat]
        ladders = _ladders(markets, f"PLAYER_ALT_{stem}", slugs)
        main_lines = _main_lines(markets, f"PLAYER_{stem}", slugs)

    prop_markets = []
    for key in list(ladders) + [k for k in main_lines if k not in ladders]:
        player, event_id = key
        game = games.get(event_id)
        if game is not None and game.started:
            continue
        main_line = main_lines.get(key)
        prop_markets.append(
            PropMarket(
                player=player,
                stat=stat,
                game_id=event_id,
                book=BOOK,
                main_line=main_line,
                ladder=ladder_without_main_line_copy(ladders.get(key, []), main_line),
                team=_team(game, slugs[key]),
            )
        )
    return prop_markets


# --- Game Lines and team markets (Sprint 6) -------------------------------------------

GAME_LINES_TAB = "popular"  # the event-page tab with the team totals and alternate team totals
_ALT_OVER = re.compile(r"^Over \((?P<line>\d+(?:\.\d+)?)\)$")


def _started(page_json: dict) -> set[str]:
    return {g.game_id for g in parse_games(page_json) if g.started}


def parse_game_lines(page_json: dict) -> list[GameLine]:
    """Each Game's main spread ("MATCH_HANDICAP_(2-WAY)") and total ("TOTAL_POINTS_(OVER/UNDER)")
    from the league page or any event-page tab. Started Games are left out."""
    games = {g.game_id: g for g in parse_games(page_json)}
    lines: dict[str, dict] = {}
    for market in _markets(page_json):
        event_id, kind = str(market.get("eventId")), market.get("marketType")
        game = games.get(event_id)
        if game is None or game.started:
            continue
        for runner in _active_runners(market):
            if kind == "MATCH_HANDICAP_(2-WAY)" and runner.get("runnerName") == game.home:
                lines.setdefault(event_id, {})["home_spread"] = float(runner["handicap"])
            elif kind == "TOTAL_POINTS_(OVER/UNDER)" and runner.get("runnerName") == "Over":
                lines.setdefault(event_id, {})["total"] = float(runner["handicap"])
    return [GameLine(event_id, BOOK, e.get("home_spread"), e.get("total")) for event_id, e in lines.items()]


def parse_team_totals(popular_json: dict) -> list[PropMarket]:
    """Each team's points as a TEAM_POINTS Prop Market, from the "popular" event-page tab:
    HOME/AWAY_TEAM_TOTAL_POINTS is the Main Line, HOME/AWAY_TEAM_ALTERNATE_TOTAL's "Over (21.5)"
    runners the Rungs (21.5 is "22 or more"). `player` is the team as FD names it in the Game."""
    games = {g.game_id: g for g in parse_games(popular_json)}
    main: dict[Key, MainLine] = {}
    rungs: dict[Key, dict[int, Rung]] = {}
    for market in _markets(popular_json):
        event_id, kind = str(market.get("eventId")), market.get("marketType", "")
        game = games.get(event_id)
        side = kind.split("_TEAM_", 1)[0]
        if game is None or game.started or side not in ("HOME", "AWAY"):
            continue
        key = (game.home if side == "HOME" else game.away, event_id)
        runners = _active_runners(market)
        if kind.endswith("_TEAM_TOTAL_POINTS"):
            sides = {r.get("runnerName"): r for r in runners}
            if {"Over", "Under"} <= set(sides) and sides["Over"]["handicap"] == sides["Under"]["handicap"]:
                main[key] = MainLine(float(sides["Over"]["handicap"]), _odds(sides["Over"]), _odds(sides["Under"]), BOOK)
        elif kind.endswith("_TEAM_ALTERNATE_TOTAL"):
            for runner in runners:
                match = _ALT_OVER.match(runner.get("runnerName", ""))
                if match is not None:
                    threshold = int(float(match["line"])) + 1
                    rungs.setdefault(key, {}).setdefault(threshold, Rung(threshold, _odds(runner), BOOK))
    return [PropMarket(team, TEAM_POINTS, event_id, BOOK, main.get((team, event_id)),
                       ladder_without_main_line_copy(list(rungs.get((team, event_id), {}).values()),
                                                     main.get((team, event_id))), team)
            for team, event_id in dict.fromkeys([*main, *rungs])]


DST_SUFFIX = " Defense"


def parse_dst_tds(td_scorer_json: dict) -> list[PropMarket]:
    """Each team's D/ST TD Rungs from the TD scorer tab: "Baltimore Defense" runners in the
    anytime/2+/3+ scorer markets. `player` is the team as FD names it there ("Baltimore",
    "LA Chargers")."""
    started = _started(td_scorer_json)
    rungs: dict[Key, dict[int, Rung]] = {}
    for market in _markets(td_scorer_json):
        threshold = TD_MARKET_THRESHOLDS.get(market.get("marketType", ""))
        event_id = str(market.get("eventId"))
        if threshold is None or event_id in started:
            continue
        for runner in _active_runners(market):
            name = runner.get("runnerName", "")
            if name.endswith(DST_SUFFIX):
                rungs.setdefault((name.removesuffix(DST_SUFFIX), event_id), {}).setdefault(
                    threshold, Rung(threshold, _odds(runner), BOOK))
    return [PropMarket(team, DST_TDS, event_id, BOOK, None, tuple(sorted(by.values(), key=lambda r: r.threshold)), team)
            for (team, event_id), by in rungs.items()]
