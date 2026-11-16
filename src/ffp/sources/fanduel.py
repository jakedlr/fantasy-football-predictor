"""FanDuel sbapi responses → Games and Prop Markets.

Pure functions over already-fetched JSON. The league page and each event-page
tab keep Games at `attachments.events{}` and markets at
`attachments.markets{}`; each market carries its own `runners[]`. See
research/sportsbook-markets.md, FanDuel section.
"""

from datetime import datetime, timezone

from ..markets import Game

BOOK = "fanduel"


def _kickoff(open_date: str) -> str:
    """FD's "2026-11-17T01:21:00.000Z" as "2026-11-17T01:21:00Z"."""
    whole_seconds = open_date.rstrip("Z").split(".")[0]
    moment = datetime.fromisoformat(whole_seconds).replace(tzinfo=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_games(page_json: dict) -> list[Game]:
    """The Games listed in a FanDuel league page or event-page tab.

    Futures and specials (no " @ " in the name) are not Games.
    """
    games = []
    for event in page_json.get("attachments", {}).get("events", {}).values():
        teams = event.get("name", "").split(" @ ")
        if len(teams) != 2:
            continue
        away, home = teams
        games.append(
            Game(
                game_id=str(event["eventId"]),
                away=away,
                home=home,
                kickoff=_kickoff(event["openDate"]),
                book=BOOK,
                started=bool(event.get("inPlay")),
            )
        )
    return games
