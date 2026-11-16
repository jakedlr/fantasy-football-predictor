"""DraftKings sportscontent API responses → Games and Prop Markets."""

import math
from datetime import datetime, timezone

from ..markets import Game, MainLine, PropMarket, Rung
from ..odds import parse_american

BOOK = "draftkings"


def _kickoff(start_event_date: str) -> str:
    """DK's "2026-11-17T01:15:00.0000000Z" (7-digit fraction) as "2026-11-17T01:15:00Z"."""
    whole_seconds = start_event_date.rstrip("Z").split(".")[0]
    moment = datetime.fromisoformat(whole_seconds).replace(tzinfo=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


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
                kickoff=_kickoff(event["startEventDate"]),
                book=BOOK,
                # Anything but NOT_STARTED (STARTED, or a finished/suspended state) is not priced.
                started=event.get("status") != "NOT_STARTED",
            )
        )
    return games


def _team(game: Game | None, venue_role: str | None) -> str | None:
    """The player's team: DK gives only "HomePlayer"/"AwayPlayer", never a team name."""
    if game is None:
        return None
    return {"HomePlayer": game.home, "AwayPlayer": game.away}.get(venue_role)


def _without_main_line_copy(ladder: list[Rung], main_line: MainLine | None) -> tuple[Rung, ...]:
    """The Ladder sorted by threshold, minus the Rung DK copies from the Main Line Over.

    DK lists Over L as a Rung at floor(L)+1 with the same odds (O/U 71.5 at
    −113 → "72+" at −113). Keeping it would count one price twice.
    """
    if main_line is not None:
        copy = (math.floor(main_line.line) + 1, main_line.over_odds)
        ladder = [r for r in ladder if (r.threshold, r.odds) != copy]
    return tuple(sorted(ladder, key=lambda r: r.threshold))


def parse_prop_markets(ladder_json: dict, main_line_json: dict, stat: str) -> list[PropMarket]:
    """One Prop Market per player per Game, from a Ladder and an O/U subcategory response."""
    event_of_market = {m["id"]: m["eventId"] for m in ladder_json.get("markets", [])}
    rungs: dict[tuple[str, str], list[Rung]] = {}
    venue_roles: dict[tuple[str, str], str | None] = {}
    games = {g.game_id: g for g in parse_games(ladder_json)}
    for sel in ladder_json.get("selections", []):
        if "milestoneValue" not in sel or not sel.get("participants"):
            continue
        event_id = event_of_market.get(sel["marketId"])
        if event_id is None:
            continue
        player = sel["participants"][0]["name"]
        rung = Rung(int(sel["milestoneValue"]), parse_american(sel["displayOdds"]["american"]), BOOK)
        rungs.setdefault((player, event_id), []).append(rung)
        venue_roles[(player, event_id)] = sel["participants"][0].get("venueRole")

    event_of_ou_market = {m["id"]: m["eventId"] for m in main_line_json.get("markets", [])}
    sides: dict[tuple[str, str], dict] = {}
    for sel in main_line_json.get("selections", []):
        if sel.get("outcomeType") not in ("Over", "Under") or not sel.get("participants"):
            continue
        event_id = event_of_ou_market.get(sel["marketId"])
        if event_id is None:
            continue
        player = sel["participants"][0]["name"]
        entry = sides.setdefault((player, event_id), {"line": float(sel["points"])})
        entry[sel["outcomeType"]] = parse_american(sel["displayOdds"]["american"])

    main_lines = {
        key: MainLine(entry["line"], entry["Over"], entry["Under"], BOOK)
        for key, entry in sides.items()
        if "Over" in entry and "Under" in entry
    }

    return [
        PropMarket(
            player=player,
            stat=stat,
            game_id=event_id,
            book=BOOK,
            main_line=main_lines.get((player, event_id)),
            ladder=_without_main_line_copy(ladder, main_lines.get((player, event_id))),
            team=_team(games.get(event_id), venue_roles[(player, event_id)]),
        )
        for (player, event_id), ladder in rungs.items()
    ]
