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
