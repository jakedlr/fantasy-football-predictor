import json
from datetime import datetime, timezone
from pathlib import Path

from ffp.markets import Game
from ffp.sources.fanduel import parse_games

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_games_reads_each_game_in_league_response_and_skips_futures():
    games = parse_games(load("fd_nfl_league.json"))
    assert games == [
        Game(
            game_id="36275768",
            away="Los Angeles Chargers",
            home="Baltimore Ravens",
            kickoff="2026-11-17T01:21:00Z",
            book="fanduel",
            started=False,
        ),
        Game(
            game_id="36305232",
            away="Indianapolis Colts",
            home="Houston Texans",
            kickoff="2026-11-20T01:16:00Z",
            book="fanduel",
            started=False,
        ),
    ]


def fd_event(event_id: int = 999, *, in_play: bool | None = None) -> dict:
    """A minimal FD event, as attachments.events holds it."""
    event = {
        "eventId": event_id,
        "name": "Kansas City Chiefs @ Denver Broncos",
        "openDate": "2026-11-16T21:25:00.000Z",
    }
    if in_play is not None:
        event["inPlay"] = in_play
    return event


def page(*events: dict, markets: tuple[dict, ...] = ()) -> dict:
    return {
        "attachments": {
            "events": {str(e["eventId"]): e for e in events},
            "markets": {m["marketId"]: m for m in markets},
        }
    }


def test_parse_games_marks_in_play_game_started():
    [game] = parse_games(page(fd_event(in_play=True)))
    assert game.started is True
    assert (game.away, game.home, game.kickoff) == (
        "Kansas City Chiefs",
        "Denver Broncos",
        "2026-11-16T21:25:00Z",
    )


def test_parse_games_marks_game_past_kickoff_started_when_now_given():
    # The league page's events carry no inPlay flag, so kickoff time is the signal there.
    before = datetime(2026, 11, 16, 21, 24, tzinfo=timezone.utc)
    at_kickoff = datetime(2026, 11, 16, 21, 25, tzinfo=timezone.utc)
    assert parse_games(page(fd_event()), now=before)[0].started is False
    assert parse_games(page(fd_event()), now=at_kickoff)[0].started is True
