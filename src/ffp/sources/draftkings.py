"""DraftKings sportscontent API responses → Games and Prop Markets."""

from datetime import datetime, timezone

from ..markets import Game, PropMarket, Rung
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


def parse_prop_markets(ladder_json: dict, main_line_json: dict, stat: str) -> list[PropMarket]:
    """One Prop Market per player per Game, from a Ladder and an O/U subcategory response."""
    event_of_market = {m["id"]: m["eventId"] for m in ladder_json.get("markets", [])}
    rungs: dict[tuple[str, str], list[Rung]] = {}
    for sel in ladder_json.get("selections", []):
        if "milestoneValue" not in sel or not sel.get("participants"):
            continue
        event_id = event_of_market.get(sel["marketId"])
        if event_id is None:
            continue
        player = sel["participants"][0]["name"]
        rung = Rung(int(sel["milestoneValue"]), parse_american(sel["displayOdds"]["american"]), BOOK)
        rungs.setdefault((player, event_id), []).append(rung)

    return [
        PropMarket(
            player=player,
            stat=stat,
            game_id=event_id,
            book=BOOK,
            main_line=None,
            ladder=tuple(sorted(ladder, key=lambda r: r.threshold)),
        )
        for (player, event_id), ladder in rungs.items()
    ]
