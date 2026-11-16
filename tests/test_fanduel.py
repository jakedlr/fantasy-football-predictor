import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ffp.markets import Game, MainLine, PropMarket, Rung
from ffp.sources.fanduel import MARKET_STEMS, TABS, parse_games, parse_prop_markets, props_posted

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


def test_tab_with_only_game_lines_has_no_props_posted():
    # Thursday's receiving tab four days out: moneyline, spread and total only.
    assert props_posted(load("fd_nfl_event_receiving_tab_not_posted.json")) is False


def test_tab_with_player_markets_has_props_posted():
    for tab in ("receiving", "rushing", "passing", "td_scorer"):
        assert props_posted(load(f"fd_nfl_event_{tab}_tab.json")) is True, tab


def rec_yds_markets() -> dict[str, PropMarket]:
    markets = parse_prop_markets(load("fd_nfl_event_receiving_tab.json"), "receiving_yards")
    return {m.player: m for m in markets}


def test_fixture_player_full_ladder_and_main_line():
    # Hand-read from the fixture: "Zay Flowers - Receiving Yds" (O/U 69.5, −113/−113) and
    # "Zay Flowers - Alt Receiving Yds" (30+ … 150+). "70+" (−114) is the Over's event,
    # Y >= 70, so the Main Line carries it and the Rung is dropped.
    zay = rec_yds_markets()["Zay Flowers"]
    assert zay == PropMarket(
        player="Zay Flowers",
        stat="receiving_yards",
        game_id="36275768",
        book="fanduel",
        main_line=MainLine(69.5, -113.0, -113.0, "fanduel"),
        ladder=tuple(
            Rung(threshold, odds, "fanduel")
            for threshold, odds in [
                (30, -1100.0),
                (40, -500.0),
                (50, -290.0),
                (60, -182.0),
                (80, 130.0),
                (90, 188.0),
                (100, 260.0),
                (110, 360.0),
                (125, 580.0),
                (150, 1100.0),
            ]
        ),
        team="Baltimore Ravens",
    )


def test_away_player_team_comes_from_logo_slug():
    # Runners carry no team field; ".../los_angeles_chargers_jersey.png" is the only signal.
    markets = rec_yds_markets()
    assert markets["Omarion Hampton"].team == "Los Angeles Chargers"
    assert markets["Mark Andrews"].team == "Baltimore Ravens"
    assert {m.team for m in markets.values()} == {"Los Angeles Chargers", "Baltimore Ravens"}


LOGO = "https://assets.sportsbook.fanduel.com/images/team/nfl/{}_jersey.png"


def runner(
    name: str, odds: int, *, handicap: float = 0, team: str = "denver_broncos", **extra
) -> dict:
    return {
        "runnerName": name,
        "handicap": handicap,
        "runnerStatus": "ACTIVE",
        "logo": LOGO.format(team),
        "isPlayerSelection": True,
        "winRunnerOdds": {"americanDisplayOdds": {"americanOdds": odds, "americanOddsInt": odds}},
        **extra,
    }


def fd_market(market_id: str, market_type: str, runners: list[dict], event_id: int = 999) -> dict:
    return {
        "marketId": market_id,
        "eventId": event_id,
        "marketType": market_type,
        "marketStatus": "OPEN",
        "runners": runners,
    }


def ladder_market(
    market_id: str, player: str, rungs: dict[int, int], tier: str = "LOW", **kw
) -> dict:
    runners = [runner(f"{player} {n}+ Yards", odds, **kw) for n, odds in rungs.items()]
    return fd_market(market_id, f"PLAYER_X_ALT_RECEIVING_YARDS_{tier}", runners)


def main_line_market(market_id: str, player: str, line: float, over: int, under: int, **kw) -> dict:
    runners = [
        runner(f"{player} Over", over, handicap=line, **kw),
        runner(f"{player} Under", under, handicap=line, **kw),
    ]
    return fd_market(market_id, "PLAYER_X_RECEIVING_YARDS_MEDIUM", runners)


def parse_one(*markets: dict, event: dict | None = None) -> list[PropMarket]:
    return parse_prop_markets(page(event or fd_event(), markets=markets), "receiving_yards")


def test_threshold_and_player_parsed_from_runner_names_with_punctuation():
    names = ["Ja'Marr Chase", "Amon-Ra St. Brown", "Marvin Harrison Jr.", "Michael Pittman Jr"]
    markets = parse_one(
        *(ladder_market(str(i), name, {70: -150, 100: 200}) for i, name in enumerate(names))
    )
    assert [m.player for m in markets] == names
    for m in markets:
        assert [(r.threshold, r.odds) for r in m.ladder] == [(70, -150.0), (100, 200.0)]


def test_tier_buckets_merge_into_one_ladder_without_duplicate_thresholds():
    markets = parse_one(
        ladder_market("1", "Courtland Sutton", {60: -200, 70: -130}, tier="LOW"),
        ladder_market("2", "Courtland Sutton", {70: -130, 80: 110, 90: 160}, tier="MEDIUM"),
        ladder_market("3", "Courtland Sutton", {100: 220}, tier="HIGH"),
    )
    [sutton] = markets
    assert [r.threshold for r in sutton.ladder] == [60, 70, 80, 90, 100]
    assert sutton.team == "Denver Broncos"


def test_rungs_sorted_by_threshold():
    [m] = parse_one(ladder_market("1", "Troy Franklin", {100: 300, 20: -500, 50: -110}))
    assert [r.threshold for r in m.ladder] == [20, 50, 100]


def test_main_line_without_ladder_still_gives_prop_market():
    [m] = parse_one(main_line_market("1", "Evan Engram", 34.5, -115, -105))
    assert m.main_line == MainLine(34.5, -115.0, -105.0, "fanduel")
    assert m.ladder == ()
    assert (m.player, m.game_id, m.team) == ("Evan Engram", "999", "Denver Broncos")


def test_started_game_excluded():
    markets = parse_one(
        ladder_market("1", "Courtland Sutton", {60: -200}),
        event=fd_event(in_play=True),
    )
    assert markets == []


def test_not_posted_tab_gives_no_prop_markets():
    not_posted = load("fd_nfl_event_receiving_tab_not_posted.json")
    assert parse_prop_markets(not_posted, "receiving_yards") == []


def test_suspended_runner_ignored():
    market = ladder_market("1", "Courtland Sutton", {60: -200, 70: -130})
    market["runners"][1]["runnerStatus"] = "SUSPENDED"
    [m] = parse_one(market)
    assert [r.threshold for r in m.ladder] == [60]


def test_main_line_needs_both_sides():
    market = main_line_market("1", "Evan Engram", 34.5, -115, -105)
    market["runners"].pop()
    assert parse_one(market) == []


def test_every_parseable_stat_names_its_tab():
    assert TABS["receiving_yards"] == "receiving-props"
    assert set(TABS) == set(MARKET_STEMS)


def test_unknown_stat_rejected():
    with pytest.raises(ValueError, match="fumbles_lost"):
        parse_prop_markets(load("fd_nfl_event_receiving_tab.json"), "fumbles_lost")
