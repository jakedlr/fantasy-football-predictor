import json
from pathlib import Path

from ffp.markets import Game
from ffp.sources.draftkings import parse_games

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_parse_games_reads_each_event_in_league_response():
    games = parse_games(load("dk_nfl_league.json"))
    assert games == [
        Game(
            game_id="34118177",
            away="LA Chargers",
            home="BAL Ravens",
            kickoff="2026-11-17T01:15:00Z",
            book="draftkings",
            started=False,
        ),
        Game(
            game_id="34118011",
            away="IND Colts",
            home="HOU Texans",
            kickoff="2026-11-20T01:15:00Z",
            book="draftkings",
            started=False,
        ),
    ]


def started_event(event_id: str = "999") -> dict:
    """A minimal DK event that is in progress (no fixture has one)."""
    return {
        "id": event_id,
        "name": "KC Chiefs @ DEN Broncos",
        "startEventDate": "2026-11-16T21:25:00.0000000Z",
        "status": "STARTED",
        "participants": [
            {"name": "DEN Broncos", "venueRole": "Home"},
            {"name": "KC Chiefs", "venueRole": "Away"},
        ],
    }


def test_parse_games_marks_in_progress_game_started():
    [game] = parse_games({"events": [started_event()]})
    assert game.started is True
    assert (game.away, game.home, game.kickoff) == ("KC Chiefs", "DEN Broncos", "2026-11-16T21:25:00Z")
