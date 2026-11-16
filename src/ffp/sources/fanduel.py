"""FanDuel sbapi responses → Games and Prop Markets.

Pure functions over already-fetched JSON. The league page and each event-page
tab keep Games at `attachments.events{}` and markets at
`attachments.markets{}`; each market carries its own `runners[]`. See
research/sportsbook-markets.md, FanDuel section.
"""

import re
from datetime import datetime, timezone

from ..markets import Game, MainLine, PropMarket, Rung

BOOK = "fanduel"

# Scoring Stat → the stem of FD's market types for it. "PLAYER_X_<stem>_<tier>" is the
# Main Line, "PLAYER_X_ALT_<stem>_<tier>" the Ladder. Rushing yards ("RUSHING_YARDS",
# rushing-props tab) and passing yards ("PASSING_YARDS", passing-props tab) follow the
# same pattern.
MARKET_STEMS = {"receiving_yards": "RECEIVING_YARDS"}

# (player name as FD spells it, FD event id)
Key = tuple[str, str]

_OVER_UNDER = re.compile(r"^(?P<player>.+) (?P<side>Over|Under)$")
# "Zay Flowers 70+ Yards", "Zay Flowers 4+ Receptions": the threshold is only in the name.
_RUNG = re.compile(r"^(?P<player>.+) (?P<threshold>\d+)\+(?: .*)?$")


def _kickoff(open_date: str) -> datetime:
    """FD's "2026-11-17T01:21:00.000Z" as a UTC datetime, to the second."""
    whole_seconds = open_date.rstrip("Z").split(".")[0]
    return datetime.fromisoformat(whole_seconds).replace(tzinfo=timezone.utc)


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
        kickoff = _kickoff(event["openDate"])
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


def _ladders(markets: list[dict], ladder_key: str, slugs: dict[Key, str | None]) -> dict[Key, list[Rung]]:
    """Each player's Rungs, merged over tiers: "N+" means Y >= N.

    A threshold seen twice for one player and Game keeps its first price.
    """
    rungs: dict[Key, dict[int, Rung]] = {}
    for market in markets:
        if market_key(market.get("marketType", "")) != ladder_key:
            continue
        event_id = str(market.get("eventId"))
        for runner in _active_runners(market):
            match = _RUNG.match(runner.get("runnerName", ""))
            if match is None:
                continue
            key = (match["player"], event_id)
            threshold = int(match["threshold"])
            rungs.setdefault(key, {}).setdefault(threshold, Rung(threshold, _odds(runner), BOOK))
            slugs.setdefault(key, _logo_slug(runner))
    return {key: list(by_threshold.values()) for key, by_threshold in rungs.items()}


def _main_lines(markets: list[dict], main_key: str, slugs: dict[Key, str | None]) -> dict[Key, MainLine]:
    """Each player's Main Line: an Over and an Under at one line in one market."""
    main_lines: dict[Key, MainLine] = {}
    for market in markets:
        if market_key(market.get("marketType", "")) != main_key:
            continue
        sides: dict[str, tuple[float, float]] = {}
        player = None
        slug = None
        for runner in _active_runners(market):
            match = _OVER_UNDER.match(runner.get("runnerName", ""))
            if match is None:
                continue
            player = match["player"]
            slug = _logo_slug(runner)
            sides[match["side"]] = (float(runner["handicap"]), _odds(runner))
        if set(sides) != {"Over", "Under"} or sides["Over"][0] != sides["Under"][0]:
            continue
        key = (player, str(market.get("eventId")))
        if key in main_lines:
            continue
        line = sides["Over"][0]
        main_lines[key] = MainLine(line, sides["Over"][1], sides["Under"][1], BOOK)
        slugs.setdefault(key, slug)
    return main_lines


def _team(game: Game | None, slug: str | None) -> str | None:
    """The player's team: FD gives only a logo, so match its slug to the Game's teams."""
    if game is None or slug is None:
        return None
    return {_slug(game.home): game.home, _slug(game.away): game.away}.get(slug)


def _without_main_line_copy(ladder: list[Rung], main_line: MainLine | None) -> tuple[Rung, ...]:
    """The Ladder sorted by threshold, minus the Rung at the Main Line Over's threshold.

    "Over 69.5" and "70+" are the same event, Y >= 70. FD's round Rungs only
    sometimes land on it (Zay Flowers: Over 69.5 at −113, "70+" at −114). The
    Main Line already carries the event, so the Rung is dropped.
    """
    if main_line is not None:
        same_event = main_line.over_threshold
        ladder = [r for r in ladder if r.threshold != same_event]
    return tuple(sorted(ladder, key=lambda r: r.threshold))


def parse_prop_markets(event_tab_json: dict, stat: str) -> list[PropMarket]:
    """One Prop Market per player per Game, from one FanDuel event-page tab.

    The Main Line and the Ladder come from the same tab. A player with only
    one of them gets main_line None or an empty ladder. Started Games are
    excluded. An empty result may mean the tab is not posted yet; ask
    props_posted() to tell.
    """
    if stat not in MARKET_STEMS:
        raise ValueError(f"no FanDuel market for Scoring Stat {stat!r}")
    stem = MARKET_STEMS[stat]
    markets = _markets(event_tab_json)
    games = {g.game_id: g for g in parse_games(event_tab_json)}
    slugs: dict[Key, str | None] = {}
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
                ladder=_without_main_line_copy(ladders.get(key, []), main_line),
                team=_team(game, slugs[key]),
            )
        )
    return prop_markets
