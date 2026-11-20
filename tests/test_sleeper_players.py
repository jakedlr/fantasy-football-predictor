"""S11.2: Sleeper's player list, downloaded at most once a day, and its ids for our Players."""

import json
from datetime import date
from pathlib import Path

import pytest

from ffp.players import Player, RosterEntry, dst_player
from ffp.sources import sleeper
from ffp.sources.http import FetchError

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"
PAYLOAD = json.loads((FIXTURES / "sleeper_players_nfl.json").read_text())
TODAY = date(2026, 11, 20)


def test_parsing_keeps_the_fantasy_positions_with_clean_ids():
    players = {p.player_id: p for p in sleeper.parse_players(PAYLOAD)}
    justice = players["5995"]  # Sleeper writes his gsis_id with a leading space, his espn_id as a number
    assert (justice.name, justice.team, justice.position) == ("Justice Hill", "BAL", "RB")
    assert justice.gsis_id == "00-0034975" and justice.espn_id == "4038441"
    assert players["9997"].gsis_id is None and players["9997"].espn_id is None  # Zay Flowers: no ids at all
    assert "BAL" not in players  # D/STs are keyed by team, not looked up
    assert {p.position for p in players.values()} == {"QB", "RB", "WR", "TE", "K"}


class FakeGet:
    def __init__(self, payload=PAYLOAD, error=None):
        self.payload, self.error, self.urls = payload, error, []

    def __call__(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.payload


def test_the_list_is_downloaded_at_most_once_a_day(tmp_path):
    get = FakeGet()
    first = sleeper.load_players(get, tmp_path, TODAY)
    again = sleeper.load_players(get, tmp_path, TODAY)
    assert get.urls == [sleeper.PLAYERS_URL]
    assert first == again and len(first) > 100
    # Only the trimmed list is kept, under the day's date; the next day downloads afresh.
    assert [p.name for p in tmp_path.iterdir()] == ["players-2026-11-20.json"]
    sleeper.load_players(get, tmp_path, date(2026, 11, 21))
    assert len(get.urls) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["players-2026-11-21.json"]  # yesterday's is removed


def test_a_failed_download_falls_back_to_an_older_list_with_a_warning(tmp_path, caplog):
    sleeper.load_players(FakeGet(), tmp_path, TODAY)
    players = sleeper.load_players(FakeGet(error=FetchError("HTTP 503")), tmp_path, date(2026, 11, 22))
    assert len(players) > 100
    assert "2026-11-20" in caplog.text and "HTTP 503" in caplog.text


def test_a_failed_download_with_nothing_saved_raises(tmp_path):
    with pytest.raises(FetchError):
        sleeper.load_players(FakeGet(error=FetchError("HTTP 503")), tmp_path, TODAY)


def test_teams_are_sleeper_s_codes():
    assert sleeper.team_code("LA") == "LAR"  # nflverse's Rams
    assert [sleeper.team_code(t) for t in ("BAL", "JAX", "WAS", "LV", "LAC")] == ["BAL", "JAX", "WAS", "LV", "LAC"]


def entry(gsis, name, position, team, sleeper_id=None, espn_id=None):
    return RosterEntry(gsis, name, position, team, "ACT", 10, sleeper_id=sleeper_id, espn_id=espn_id)


@pytest.fixture(scope="module")
def ids():
    return sleeper.SleeperIds(sleeper.parse_players(PAYLOAD))


def test_a_player_is_found_by_his_gsis_id(ids):
    hill = entry("00-0034975", "Justice Hill", "RB", "BAL")
    assert ids.lookup(hill) == ("5995", "4038441")


def test_without_a_gsis_id_in_sleeper_the_roster_s_sleeper_id_is_used(ids):
    zay = entry("00-0039064", "Zay Flowers", "WR", "BAL", sleeper_id="9997", espn_id="4429615")
    assert ids.lookup(zay) == ("9997", "4429615")  # Sleeper has no espn_id for him: nflverse's is used


def test_a_roster_sleeper_id_sleeper_does_not_list_is_not_trusted(ids):
    zay = entry("00-0039064", "Zay Flowers", "WR", "BAL", sleeper_id="99999999")
    assert ids.lookup(zay) == ("9997", None)  # found by name and team instead


def test_failing_ids_a_player_is_matched_by_name_and_team(ids):
    assert ids.lookup(entry("00-0039064", "Zay Flowers", "WR", "BAL")) == ("9997", None)
    assert ids.lookup(entry("00-0036355", "Justin Herbert", "QB", "LAC")) == ("6797", "4038941")
    assert ids.lookup(entry("00-0040727", "Tre Harris", "WR", "LAC"))[0] == "12509"  # Sleeper: "Tre' Harris"
    assert ids.lookup(entry("00-0049999", "Not Listed", "WR", "LAC")) == (None, None)
    assert ids.lookup(entry("x", "Zay Flowers", "WR", "LAC")) == (None, None)  # another team's


def test_a_rams_player_is_matched_on_sleeper_s_lar(ids):
    karty = next(p for p in sleeper.parse_players(PAYLOAD) if p.name == "Joshua Karty")
    assert ids.lookup(entry("y", "Joshua Karty", "K", "LA")) == (karty.player_id, karty.espn_id)


def test_the_id_map_covers_every_listed_player_on_the_teams_with_an_espn_id(ids):
    rams_only = ids.id_map({"LA"}, roster=[])
    assert rams_only and all(v.isdigit() for v in rams_only.values())
    players = {p.player_id: p for p in sleeper.parse_players(PAYLOAD)}
    assert {players[k].team for k in rams_only} == {"LAR"}
    # Sleeper's espn_id first; else the nflverse roster's, joined by Sleeper id or gsis_id.
    bal = ids.id_map({"BAL"}, roster=[entry("00-0039064", "Zay Flowers", "WR", "BAL", sleeper_id="9997",
                                            espn_id="4429615")])
    assert bal["9997"] == "4429615" and bal["5995"] == "4038441"
    assert "9997" not in ids.id_map({"BAL"}, roster=[])


def test_a_dst_s_sleeper_id_is_its_team():
    assert sleeper.dst_id(dst_player("BAL")) == "BAL"
    assert sleeper.dst_id(dst_player("LA")) == "LAR"
    assert sleeper.dst_id(Player("DST-WAS", "WAS D/ST", "DST", "WAS")) == "WAS"


def test_a_player_listed_at_another_position_counts_by_his_fantasy_position():
    # Sleeper lists Travis Hunter as a DB (fantasy positions DB and WR), Kyle Juszczyk as a FB (RB).
    payload = {"12530": {"player_id": "12530", "full_name": "Travis Hunter", "position": "DB", "team": "JAX",
                         "fantasy_positions": ["DB", "WR"], "gsis_id": None, "espn_id": 4685415},
               "1379": {"player_id": "1379", "full_name": "Kyle Juszczyk", "position": "FB", "team": "SF",
                        "fantasy_positions": ["RB"]},
               "99": {"player_id": "99", "full_name": "A Linebacker", "position": "LB", "team": "SF",
                      "fantasy_positions": ["LB"]}}
    players = {p.name: p.position for p in sleeper.parse_players(payload)}
    assert players == {"Travis Hunter": "WR", "Kyle Juszczyk": "RB"}


def test_the_roster_keeps_nflverse_s_sleeper_and_espn_ids():
    from ffp.players import load_roster

    rows = [{"gsis_id": "00-0039064", "full_name": "Zay Flowers", "position": "WR", "team": "BAL", "status": "ACT",
             "week": 11, "sleeper_id": "9997", "espn_id": "4429615"},
            {"gsis_id": "00-0040727", "full_name": "Tre Harris", "position": "WR", "team": "LAC", "status": "ACT",
             "week": 11, "sleeper_id": None, "espn_id": float("nan")},
            {"gsis_id": "00-0036355", "full_name": "Justin Herbert", "position": "QB", "team": "LAC", "status": "ACT",
             "week": 11}]  # the fixture CSVs have no id columns
    zay, tre, herbert = load_roster(2026, fetch=lambda season: rows)
    assert (zay.sleeper_id, zay.espn_id) == ("9997", "4429615")
    assert (tre.sleeper_id, tre.espn_id) == (None, None)
    assert (herbert.sleeper_id, herbert.espn_id) == (None, None)
