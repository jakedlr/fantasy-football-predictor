"""DraftKings sportscontent API responses → Games and Prop Markets.

Pure functions over already-fetched JSON. A subcategory response is flat:
`events[]` are Games, `markets[]` join to an event on `eventId`, and
`selections[]` join to a market on `marketId`. See
research/sportsbook-markets.md, DraftKings section.
"""

from collections.abc import Iterator

from ..markets import Game, MainLine, PropMarket, Rung, format_utc, ladder_without_main_line_copy, parse_utc
from ..odds import parse_american

BOOK = "draftkings"

# (player name as DK spells it, DK event id)
Key = tuple[str, str]


def parse_games(subcategory_json: dict) -> list[Game]:
    """The Games (DK "events") listed in a DraftKings league or subcategory response."""
    games = []
    for event in subcategory_json.get("events", []):
        teams = {p["venueRole"]: p["name"] for p in event["participants"]}
        games.append(
            Game(
                game_id=event["id"],
                away=teams["Away"],
                home=teams["Home"],
                kickoff=format_utc(parse_utc(event["startEventDate"])),  # DK sends a 7-digit fraction
                book=BOOK,
                # Anything but NOT_STARTED (STARTED, or a finished/suspended state) is not priced.
                started=event.get("status") != "NOT_STARTED",
            )
        )
    return games


def _player_selections(response: dict) -> Iterator[tuple[Key, str | None, dict]]:
    """(key, venueRole, selection) for each selection that names a player.

    D/ST and "No Touchdown Scorer" selections have no participants and are skipped.
    """
    event_of_market = {m["id"]: m["eventId"] for m in response.get("markets", [])}
    for sel in response.get("selections", []):
        event_id = event_of_market.get(sel.get("marketId"))
        if event_id is None or not sel.get("participants"):
            continue
        player = sel["participants"][0]
        yield (player["name"], event_id), player.get("venueRole"), sel


def _odds(sel: dict) -> float:
    return parse_american(sel["displayOdds"]["american"])


# TD scorer selections that are Rungs of "rush_rec_tds", by outcomeType: "N or more TDs".
# "First Scorer" (and Last TD, which we never fetch) is not a "N or more" event, so it is not a Rung.
TD_SCORER_THRESHOLDS = {"Anytime Scorer": 1, "To Score 2 Or More": 2, "To Score 3 Or More": 3}


def _threshold(sel: dict) -> int | None:
    """A Ladder selection's N in "Y >= N": milestoneValue, or a TD scorer's outcomeType."""
    if "milestoneValue" in sel:
        return int(sel["milestoneValue"])
    return TD_SCORER_THRESHOLDS.get(sel.get("outcomeType"))


def _ladders(ladder_json: dict, venue_roles: dict[Key, str | None]) -> dict[Key, list[Rung]]:
    """Each player's Rungs: milestoneValue N, or a TD scorer's "N or more", means Y >= N.

    A threshold seen twice for one player and Game (the payload can merge several
    subcategories) keeps its first price, as on FanDuel.
    """
    rungs: dict[Key, dict[int, Rung]] = {}
    for key, venue_role, sel in _player_selections(ladder_json):
        threshold = _threshold(sel)
        if threshold is None:
            continue
        rungs.setdefault(key, {}).setdefault(threshold, Rung(threshold, _odds(sel), BOOK))
        venue_roles.setdefault(key, venue_role)
    return {key: list(by_threshold.values()) for key, by_threshold in rungs.items()}


def _is_main(sel: dict) -> bool:
    return bool(sel.get("main")) or "MainPointLine" in (sel.get("tags") or ())


def _main_pair(sels: list[dict]) -> list[dict]:
    """The selections that make up a player's Main Line.

    Those DK marks as main. If DK marks none and the player has exactly one
    Over/Under pair at one line, that pair is the Main Line. With several
    unmarked pairs there's no telling which is main, so there's none.
    """
    marked = [s for s in sels if _is_main(s)]
    if marked:
        return marked
    if sorted(s["outcomeType"] for s in sels) == ["Over", "Under"] and len(
        {float(s["points"]) for s in sels}
    ) == 1:
        return sels
    return []


def _main_lines(main_line_json: dict, venue_roles: dict[Key, str | None]) -> dict[Key, MainLine]:
    """Each player's Main Line, where the response has both its Over and its Under.

    Only selections DK marks as the main line count; alternate Over/Under pairs
    at other lines are ignored so they can't mix into the Main Line.
    """
    by_player: dict[Key, list[dict]] = {}
    roles: dict[Key, str | None] = {}
    for key, venue_role, sel in _player_selections(main_line_json):
        if sel.get("outcomeType") in ("Over", "Under"):
            by_player.setdefault(key, []).append(sel)
            roles.setdefault(key, venue_role)

    main_lines = {}
    for key, sels in by_player.items():
        entry = {}
        for sel in _main_pair(sels):
            entry.setdefault("line", float(sel["points"]))
            entry[sel["outcomeType"]] = _odds(sel)
        if "Over" in entry and "Under" in entry:
            main_lines[key] = MainLine(entry["line"], entry["Over"], entry["Under"], BOOK)
            venue_roles.setdefault(key, roles[key])
    return main_lines


def _team(game: Game | None, venue_role: str | None) -> str | None:
    """The player's team: DK gives only "HomePlayer"/"AwayPlayer", never a team name."""
    if game is None:
        return None
    return {"HomePlayer": game.home, "AwayPlayer": game.away}.get(venue_role)



def parse_prop_markets(ladder_json: dict, main_line_json: dict | None, stat: str) -> list[PropMarket]:
    """One Prop Market per player per Game, from a Ladder and an O/U subcategory response.

    A player in only one response gets main_line None or an empty ladder.
    Started Games are excluded.

    Touchdowns ("rush_rec_tds") have no O/U: pass main_line_json None and, as the
    Ladder, the TD Scorer response (merged with 3+ TDs when fetched). Its anytime,
    "2 or more" and "3 or more" prices become Rungs 1, 2 and 3.
    """
    main_line_json = main_line_json or {}
    games = {g.game_id: g for g in parse_games(main_line_json) + parse_games(ladder_json)}
    venue_roles: dict[Key, str | None] = {}
    ladders = _ladders(ladder_json, venue_roles)
    main_lines = _main_lines(main_line_json, venue_roles)

    markets = []
    for key in list(ladders) + [k for k in main_lines if k not in ladders]:
        player, event_id = key
        game = games.get(event_id)
        if game is not None and game.started:
            continue
        main_line = main_lines.get(key)
        markets.append(
            PropMarket(
                player=player,
                stat=stat,
                game_id=event_id,
                book=BOOK,
                main_line=main_line,
                ladder=ladder_without_main_line_copy(ladders.get(key, []), main_line),
                team=_team(game, venue_roles[key]),
            )
        )
    return markets
