import json
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
