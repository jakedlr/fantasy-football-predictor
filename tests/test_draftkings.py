import json
from pathlib import Path

from ffp.markets import Game, MainLine, PropMarket, Rung
from ffp.sources.draftkings import parse_games, parse_prop_markets

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


def rec_yds_markets() -> dict[str, PropMarket]:
    markets = parse_prop_markets(
        load("dk_nfl_rec_yds_ladder.json"), load("dk_nfl_rec_yds_ou.json"), "receiving_yards"
    )
    return {m.player: m for m in markets}


def test_ladder_rungs_for_named_player_mean_threshold_or_more():
    zay = rec_yds_markets()["Zay Flowers"]
    assert (zay.stat, zay.game_id, zay.book) == ("receiving_yards", "34118177", "draftkings")
    rungs = {r.threshold: r.odds for r in zay.ladder}
    # "70+" (milestoneValue 70) at −124 means P(Y >= 70); the Unicode minus is parsed.
    assert rungs[70] == -124.0
    assert rungs[20] == -2700.0
    assert rungs[80] == 122.0
    assert rungs[180] == 3500.0
    assert all(r.book == "draftkings" for r in zay.ladder)
    thresholds = [r.threshold for r in zay.ladder]
    assert thresholds == sorted(thresholds)
    assert len(rec_yds_markets()) == 13


def test_main_line_comes_from_ou_response():
    assert rec_yds_markets()["Zay Flowers"].main_line == MainLine(
        line=71.5, over_odds=-113.0, under_odds=-111.0, book="draftkings"
    )


def test_team_comes_from_venue_role():
    markets = rec_yds_markets()
    assert markets["Zay Flowers"].team == "BAL Ravens"  # HomePlayer
    assert markets["Quentin Johnston"].team == "LA Chargers"  # AwayPlayer


def test_ladder_drops_the_rung_that_duplicates_the_main_line_over():
    # Zay Flowers O/U 71.5 Over −113; DK repeats it in the Ladder as "72+" at −113.
    zay = rec_yds_markets()["Zay Flowers"]
    assert [r.threshold for r in zay.ladder] == [
        20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180
    ]


def test_ladder_keeps_floor_plus_one_rung_whose_odds_differ_from_the_over():
    # Tre Harris O/U 37.5 Over −111, but his "38+" Rung is −109: not a copy, so it stays.
    tre = rec_yds_markets()["Tre Harris"]
    assert tre.main_line.over_odds == -111.0
    assert Rung(38, -109.0, "draftkings") in tre.ladder


def without_player(response: dict, player: str) -> dict:
    """A copy of a DK response with one player's selections removed."""
    selections = [
        s for s in response["selections"]
        if not s.get("participants") or s["participants"][0]["name"] != player
    ]
    return {**response, "selections": selections}


def test_player_only_in_ladder_has_no_main_line():
    ou = without_player(load("dk_nfl_rec_yds_ou.json"), "Zay Flowers")
    markets = parse_prop_markets(load("dk_nfl_rec_yds_ladder.json"), ou, "receiving_yards")
    zay = next(m for m in markets if m.player == "Zay Flowers")
    assert zay.main_line is None
    # With no Main Line there is nothing to de-duplicate, so "72+" stays.
    assert Rung(72, -113.0, "draftkings") in zay.ladder


def test_player_only_in_main_line_has_empty_ladder():
    ladder = without_player(load("dk_nfl_rec_yds_ladder.json"), "Derrick Henry")
    markets = parse_prop_markets(ladder, load("dk_nfl_rec_yds_ou.json"), "receiving_yards")
    henry = next(m for m in markets if m.player == "Derrick Henry")
    assert henry == PropMarket(
        player="Derrick Henry",
        stat="receiving_yards",
        game_id="34118177",
        book="draftkings",
        main_line=MainLine(5.5, -118.0, -106.0, "draftkings"),
        ladder=(),
        team="BAL Ravens",
    )


def test_started_games_are_excluded():
    event = started_event("999")
    ladder = {
        "events": [event],
        "markets": [{"id": "m1", "eventId": "999"}],
        "selections": [
            {
                "marketId": "m1",
                "label": "50+",
                "milestoneValue": 50,
                "displayOdds": {"american": "+150"},
                "participants": [{"name": "Travis Kelce", "venueRole": "AwayPlayer"}],
            }
        ],
    }
    main_line = {
        "events": [event],
        "markets": [{"id": "m2", "eventId": "999"}],
        "selections": [
            {
                "marketId": "m2",
                "label": side,
                "outcomeType": side,
                "points": 45.5,
                "displayOdds": {"american": odds},
                "participants": [{"name": name, "venueRole": "AwayPlayer"}],
            }
            for name in ("Travis Kelce", "Xavier Worthy")
            for side, odds in (("Over", "−115"), ("Under", "−105"))
        ],
    }
    assert parse_prop_markets(ladder, main_line, "receiving_yards") == []
