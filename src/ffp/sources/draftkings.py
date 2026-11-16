"""DraftKings sportscontent API responses → Games and Prop Markets."""

from datetime import datetime, timezone

from ..markets import Game

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
