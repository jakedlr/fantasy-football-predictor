"""FanDuel sbapi responses → Games and Prop Markets.

Pure functions over already-fetched JSON. The league page and each event-page
tab keep Games at `attachments.events{}` and markets at
`attachments.markets{}`; each market carries its own `runners[]`. See
research/sportsbook-markets.md, FanDuel section.
"""

from datetime import datetime, timezone

from ..markets import Game

BOOK = "fanduel"


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
