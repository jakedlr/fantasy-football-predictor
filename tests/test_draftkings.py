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


def test_ladder_drops_floor_plus_one_rung_even_when_its_odds_differ_from_the_over():
    # Tre Harris O/U 37.5 Over −111 and "38+" at −109 price the same event (Y >= 38).
    # The Main Line already carries it (with extra weight), so the Rung is dropped.
    tre = rec_yds_markets()["Tre Harris"]
    assert tre.main_line.over_odds == -111.0
    assert 38 not in [r.threshold for r in tre.ladder]


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


def with_alternate_line(response: dict, player: str, line: float, over: str, under: str) -> dict:
    """A copy of a DK O/U response with a non-main Over/Under pair for `player` listed first."""
    originals = [
        s for s in response["selections"]
        if s.get("participants") and s["participants"][0]["name"] == player
    ]
    alternates = []
    for sel, odds in zip(sorted(originals, key=lambda s: s["outcomeType"] != "Over"), (over, under)):
        alt = json.loads(json.dumps(sel))
        alt.update(points=line, main=False, tags=[t for t in sel["tags"] if t != "MainPointLine"])
        alt["displayOdds"]["american"] = odds
        alternates.append(alt)
    return {**response, "selections": alternates + response["selections"]}


def test_main_line_ignores_alternate_over_under_lines():
    # Review finding: an alternate pair (64.5) mixed its line and odds into the Main Line.
    ou = with_alternate_line(load("dk_nfl_rec_yds_ou.json"), "Zay Flowers", 64.5, "−160", "+125")
    markets = parse_prop_markets(load("dk_nfl_rec_yds_ladder.json"), ou, "receiving_yards")
    zay = next(m for m in markets if m.player == "Zay Flowers")
    assert zay.main_line == MainLine(71.5, -113.0, -111.0, "draftkings")


def without_main_flags(response: dict, player: str, tags=()) -> dict:
    """A copy of a DK O/U response with `player`'s main/MainPointLine marks removed."""
    copy = json.loads(json.dumps(response))
    for sel in copy["selections"]:
        if sel.get("participants") and sel["participants"][0]["name"] == player:
            sel.pop("main", None)
            sel["tags"] = tags
    return copy


def zay_main_line(ou: dict) -> MainLine | None:
    markets = parse_prop_markets(load("dk_nfl_rec_yds_ladder.json"), ou, "receiving_yards")
    return next(m for m in markets if m.player == "Zay Flowers").main_line


def test_an_unmarked_lone_over_under_pair_is_the_main_line():
    # Review finding: a pair without DK's main marks was silently dropped.
    ou = without_main_flags(load("dk_nfl_rec_yds_ou.json"), "Zay Flowers")
    assert zay_main_line(ou) == MainLine(71.5, -113.0, -111.0, "draftkings")


def test_null_tags_do_not_crash_the_parse():
    # Review finding: "tags": null raised TypeError and stopped the whole parse.
    ou = without_main_flags(load("dk_nfl_rec_yds_ou.json"), "Zay Flowers", tags=None)
    assert zay_main_line(ou) == MainLine(71.5, -113.0, -111.0, "draftkings")


def test_several_unmarked_pairs_give_no_main_line():
    # With no marks there's no telling which pair is main, so none is guessed.
    ou = with_alternate_line(load("dk_nfl_rec_yds_ou.json"), "Zay Flowers", 64.5, "−160", "+125")
    ou = without_main_flags(ou, "Zay Flowers")
    assert zay_main_line(ou) is None


# --- receptions (S3.1) -------------------------------------------------------------


def receptions() -> list[PropMarket]:
    return parse_prop_markets(
        load("dk_nfl_receptions_ladder.json"), load("dk_nfl_receptions_ou.json"), "receptions"
    )


def test_receptions_ladder_and_main_line_for_named_player():
    # Ladder 3+..12+ with 6+ at −129; O/U 5.5 at −128/+100 (recorded a little later).
    zay = next(m for m in receptions() if m.player == "Zay Flowers")
    assert zay.stat == "receptions"
    assert zay.team == "BAL Ravens"
    assert zay.main_line == MainLine(5.5, -128.0, 100.0, "draftkings")
    assert [(r.threshold, r.odds) for r in zay.ladder] == [
        (3, -2000.0), (4, -610.0), (5, -257.0), (7, 145.0), (8, 257.0),
        (9, 456.0), (10, 800.0), (11, 1340.0), (12, 2100.0),
    ]  # 6+ is the Main Line Over's event and is dropped


def test_receptions_player_without_an_ou_keeps_his_ladder():
    bateman = next(m for m in receptions() if m.player == "Rashod Bateman")
    assert bateman.main_line is None
    assert [r.threshold for r in bateman.ladder] == [2, 3, 4, 5, 6]


def test_receptions_main_line_at_half_a_catch():
    hibner = next(m for m in receptions() if m.player == "Matthew Hibner")
    assert hibner.main_line.line == 0.5 and hibner.main_line.over_threshold == 1
    assert [r.threshold for r in hibner.ladder] == [2, 3, 4]


# --- touchdown scorers (S3.2) --------------------------------------------------------


def td_scorer_and_3plus() -> dict:
    """The TD Scorer (12438) and 3+ TDs (12422) responses as one, the way live.py merges them."""
    a, b = load("dk_nfl_td_scorer.json"), load("dk_nfl_3plus_tds.json")
    return {"events": a["events"], "markets": a["markets"] + b["markets"],
            "selections": a["selections"] + b["selections"]}


def tds() -> list[PropMarket]:
    return parse_prop_markets(td_scorer_and_3plus(), None, "rush_rec_tds")


def test_anytime_two_plus_and_three_plus_are_rungs_one_two_and_three():
    henry = next(m for m in tds() if m.player == "Derrick Henry")
    assert henry == PropMarket(
        player="Derrick Henry", stat="rush_rec_tds", game_id="34118177", book="draftkings",
        main_line=None,
        ladder=(Rung(1, -150.0, "draftkings"), Rung(2, 390.0, "draftkings"), Rung(3, 1700.0, "draftkings")),
        team="BAL Ravens",
    )


def test_first_td_scorer_is_not_a_rung():
    # Every player has a 1st TD price (+360 for Henry); only thresholds 1, 2, 3 come out.
    assert {r.threshold for m in tds() for r in m.ladder} == {1, 2, 3}
    assert all(r.odds != 360.0 for r in next(m for m in tds() if m.player == "Derrick Henry").ladder)


def test_dst_and_no_touchdown_scorer_are_skipped():
    players = {m.player for m in tds()}
    assert len(players) == 26
    assert not any("D/ST" in p or "No Touchdown" in p for p in players)


def test_a_player_without_a_three_plus_price_has_rungs_one_and_two():
    hibner = next(m for m in tds() if m.player == "Matthew Hibner")
    assert [(r.threshold, r.odds) for r in hibner.ladder] == [(1, 1300.0), (2, 16000.0)]


def test_td_scorer_response_alone_gives_anytime_and_two_plus():
    markets = parse_prop_markets(load("dk_nfl_td_scorer.json"), None, "rush_rec_tds")
    zay = next(m for m in markets if m.player == "Zay Flowers")
    assert [(r.threshold, r.odds) for r in zay.ladder] == [(1, 155.0), (2, 1300.0)]


def test_a_threshold_listed_twice_keeps_its_first_price():
    # Review finding 5: merging the same subcategory twice (or 3+ TDs into TD Scorer) gave duplicate Rungs.
    payload = td_scorer_and_3plus()
    twice = {**payload, "selections": payload["selections"] + payload["selections"]}
    henry = next(m for m in parse_prop_markets(twice, None, "rush_rec_tds") if m.player == "Derrick Henry")
    assert [(r.threshold, r.odds) for r in henry.ladder] == [(1, -150.0), (2, 390.0), (3, 1700.0)]
