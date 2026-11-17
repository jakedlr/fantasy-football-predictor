import csv
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ffp.players import (
    PROPS_NOT_POSTED,
    AmbiguousPlayerError,
    Player,
    PlayerError,
    RosterEntry,
    ScheduledGame,
    UnknownPlayerError,
    UnknownTeamError,
    UnsupportedPlayerError,
    kickoff_utc,
    load_roster,
    load_schedule,
    next_game,
    resolve_player,
    team_code_from_book_name,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read_csv(name):
    with open(FIXTURES / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def roster():
    return load_roster(2026, fetch=lambda season: _read_csv(f"rosters_weekly_{season}.csv"))


def test_resolves_name_with_apostrophe(roster):
    assert resolve_player("Ja'Marr Chase", roster) == Player(
        gsis_id="00-0036900", name="Ja'Marr Chase", position="WR", team="CIN"
    )


@pytest.mark.parametrize("text", ["AJ Brown", "A.J. Brown", "a.j. brown"])
def test_resolves_initials_with_or_without_periods(roster, text):
    assert resolve_player(text, roster).gsis_id == "00-0035676"


def test_skill_position_wins_over_a_defender_with_the_same_name(roster):
    player = resolve_player("Justin Jefferson", roster)
    assert (player.gsis_id, player.position, player.team) == ("00-0036322", "WR", "MIN")


def test_unknown_name_raises(roster):
    with pytest.raises(UnknownPlayerError, match="Joe Nobody"):
        resolve_player("Joe Nobody", roster)


# nflverse has no 2026 skill-player collision after normalizing (research/nflverse-data.md
# section 2), so the second "Josh Johnson" is made up; the first is the real ARI QB.
JOSH_JOHNSON_QB = RosterEntry("00-0026300", "Josh Johnson", "QB", "ARI", "ACT", 10)
JOSH_JOHNSON_RB = RosterEntry("00-0099999", "Josh Johnson Jr.", "RB", "BAL", "ACT", 10)


def test_ambiguous_name_raises_and_lists_candidates(roster):
    roster = [*roster, JOSH_JOHNSON_QB, JOSH_JOHNSON_RB]
    with pytest.raises(AmbiguousPlayerError) as err:
        resolve_player("Josh Johnson", roster)
    message = str(err.value)
    assert "Josh Johnson (QB, ARI)" in message
    assert "Josh Johnson Jr. (RB, BAL)" in message
    assert "(TEAM)" in message


@pytest.mark.parametrize("text, gsis_id", [
    ("Josh Johnson (BAL)", "00-0099999"),
    ("Josh Johnson (ari)", "00-0026300"),
    ("Josh Johnson  ( ARI )", "00-0026300"),
])
def test_team_hint_breaks_the_tie(roster, text, gsis_id):
    roster = [*roster, JOSH_JOHNSON_QB, JOSH_JOHNSON_RB]
    assert resolve_player(text, roster).gsis_id == gsis_id


def test_team_hint_on_a_unique_name_still_checks_the_team(roster):
    assert resolve_player("Ja'Marr Chase (CIN)", roster).gsis_id == "00-0036900"
    with pytest.raises(UnknownPlayerError, match="CIN"):
        resolve_player("Ja'Marr Chase (PIT)", roster)


def test_a_kicker_resolves_like_any_player(roster):
    # Sprint 6: kickers are supported.
    assert resolve_player("Evan McPherson", roster) == Player("00-0036854", "Evan McPherson", "K", "CIN")


@pytest.mark.parametrize("text, team", [
    ("Ravens D/ST", "BAL"), ("BAL DST", "BAL"), ("BAL D/ST", "BAL"), ("Baltimore Ravens Defense", "BAL"),
    ("Ravens", "BAL"), ("Ravens DEF", "BAL"), ("Ravens D-ST", "BAL"), ("Bengals Defense", "CIN"),
    ("Baltimore D/ST", "BAL"), ("D/ST (BAL)", "BAL"), ("Baltimore Defense", "BAL"), ("LA Chargers D/ST", "LAC"),
])
def test_a_dst_resolves_to_its_team(roster, text, team):
    assert resolve_player(text, roster) == Player(f"DST-{team}", f"{team} D/ST", "DST", team)


def test_a_dst_without_a_team_is_unknown(roster):
    with pytest.raises(UnknownPlayerError, match="names no team"):
        resolve_player("D/ST", roster)


def test_name_on_only_a_non_skill_position_is_not_supported(roster):
    only_lb = [e for e in roster if e.gsis_id != "00-0036322"]  # drop the WR Justin Jefferson
    with pytest.raises(UnsupportedPlayerError, match="LB"):
        resolve_player("Justin Jefferson", only_lb)


@pytest.mark.parametrize("text, gsis_id", [
    ("Hollywood Brown", "00-0035662"),
    ("Chigoziem Okonkwo", "00-0037809"),
    ("Chig Okonkwo", "00-0037809"),
    ("Kenneth Walker", "00-0038134"),
    ("Kenneth Walker III", "00-0038134"),
])
def test_aliases_and_suffixes(roster, text, gsis_id):
    assert resolve_player(text, roster).gsis_id == gsis_id


def test_team_hint_may_be_a_nickname_or_book_name(roster):
    roster = [*roster, JOSH_JOHNSON_QB, JOSH_JOHNSON_RB]
    assert resolve_player("Josh Johnson (Ravens)", roster).gsis_id == "00-0099999"
    assert resolve_player("Josh Johnson (Arizona Cardinals)", roster).gsis_id == "00-0026300"


def test_load_roster_keeps_each_players_latest_team_and_drops_cut_players(roster):
    by_id = {e.gsis_id: e for e in roster}
    assert by_id["00-0038519"].team == "HOU"  # Nikko Remigio, KC weeks 1-9, HOU week 10
    assert "00-0036924" not in by_id  # Michael Carter, CUT
    assert by_id["00-0035662"].team == "PHI"  # Marquise Brown, latest row is week 9 (bye)
    assert by_id["00-0034796"].status == "RES"  # Lamar Jackson on reserve is still resolvable


FULL_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


@pytest.mark.parametrize("code, full_name", sorted(FULL_NAMES.items()))
def test_team_code_from_fanduel_full_name_and_from_code(code, full_name):
    assert team_code_from_book_name(full_name) == code
    assert team_code_from_book_name(code) == code
    assert team_code_from_book_name(full_name.split()[-1]) == code  # nickname alone


@pytest.mark.parametrize("book_name, code", [
    # DraftKings spells a Game's teams "<code> <nickname>" (research/fixtures/dk_nfl_league.json)
    ("BAL Ravens", "BAL"), ("LA Chargers", "LAC"), ("LA Rams", "LA"), ("NY Giants", "NYG"),
    ("NY Jets", "NYJ"), ("WAS Commanders", "WAS"), ("SF 49ers", "SF"), ("JAX Jaguars", "JAX"),
    # other common abbreviations
    ("LAR", "LA"), ("JAC", "JAX"), ("WSH", "WAS"), ("ARZ", "ARI"), ("LVR", "LV"),
    ("  bal  ravens ", "BAL"),
])
def test_team_code_from_draftkings_name(book_name, code):
    assert team_code_from_book_name(book_name) == code


@pytest.mark.parametrize("book_name", ["", "Springfield Isotopes", "XYZ", "Dallas Eagles"])
def test_unknown_team_name_raises(book_name):
    with pytest.raises(UnknownTeamError):
        team_code_from_book_name(book_name)


def test_player_errors_share_a_value_error_base():
    assert issubclass(UnknownPlayerError, PlayerError)
    assert issubclass(AmbiguousPlayerError, PlayerError)
    assert issubclass(UnsupportedPlayerError, PlayerError)
    assert issubclass(PlayerError, ValueError)


# S2.4 Next Game

def _schedule():
    return load_schedule(2026, fetch=lambda season: _read_csv(f"schedule_{season}.csv"))


SUNDAY_EVENING = datetime(2026, 11, 15, 23, 0, tzinfo=UTC)  # 18:00 ET, Sunday of week 10


def test_next_game_is_the_next_unscored_game():
    game = next_game("BAL", _schedule(), SUNDAY_EVENING)
    assert game == ScheduledGame(
        game_id="2026_10_LAC_BAL", season=2026, week=10, home="BAL", away="LAC",
        kickoff="2026-11-17T01:15:00Z",  # Monday 20:15 ET
    )
    assert game.opponent("BAL") == "LAC"
    assert game.opponent("LAC") == "BAL"


def test_next_game_skips_a_scored_game():
    game = next_game("CIN", _schedule(), SUNDAY_EVENING)
    assert (game.game_id, game.opponent("CIN"), game.kickoff) == (
        "2026_11_CIN_WAS", "WAS", "2026-11-24T01:15:00Z"
    )


def test_a_game_in_progress_is_not_next():
    during_monday_night = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
    assert next_game("BAL", _schedule(), during_monday_night).game_id == "2026_11_BAL_CAR"


def test_no_remaining_game_gives_none():
    assert next_game("BAL", _schedule(), datetime(2027, 3, 1, tzinfo=UTC)) is None
    assert next_game("PIT", _schedule(), SUNDAY_EVENING) is None  # only a scored PIT game


def test_naive_now_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        next_game("BAL", _schedule(), datetime(2026, 11, 15, 18, 0))


@pytest.mark.parametrize("gameday, gametime, utc", [
    ("2026-10-25", "13:00", "2026-10-25T17:00:00Z"),  # EDT, UTC-4
    ("2026-11-01", "13:00", "2026-11-01T18:00:00Z"),  # EST from 02:00 that morning
    ("2026-03-08", "13:00", "2026-03-08T17:00:00Z"),  # EDT from 02:00 that morning
    ("2026-11-16", "20:15", "2026-11-17T01:15:00Z"),  # evening game lands on the next UTC day
    ("2026-11-08", "09:30", "2026-11-08T14:30:00Z"),  # international morning game
])
def test_kickoff_converts_us_eastern_to_utc_across_dst(gameday, gametime, utc):
    assert kickoff_utc(gameday, gametime) == utc


def test_props_not_posted_message():
    assert PROPS_NOT_POSTED == "props not posted yet for this Game; rerun closer to kickoff"


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def to_dicts(self):
        return self._rows


def test_loaders_default_to_nflreadpy(monkeypatch):
    fake = types.SimpleNamespace(
        load_rosters_weekly=lambda season: _FakeFrame(_read_csv(f"rosters_weekly_{season}.csv")),
        load_schedules=lambda season: _FakeFrame(_read_csv(f"schedule_{season}.csv")),
    )
    monkeypatch.setitem(sys.modules, "nflreadpy", fake)
    assert resolve_player("Zay Flowers", load_roster(2026)).team == "BAL"
    assert next_game("BAL", load_schedule(2026), SUNDAY_EVENING).game_id == "2026_10_LAC_BAL"


# Sprint 2 review findings.


def _entry(gsis_id, name, position="WR", team="LAC"):
    from ffp.players import RosterEntry

    return RosterEntry(gsis_id, name, position, team, "ACT", 10)


@pytest.mark.parametrize("text", ["Joshua Palmer", "Josh Palmer"])
def test_a_roster_name_that_is_an_alias_spelling_still_resolves(text):
    # Finding 5: aliases were applied to the input but not to roster names.
    from ffp.players import resolve_player

    player = resolve_player(text, [_entry("00-1", "Joshua Palmer")])
    assert player.gsis_id == "00-1"


def test_rows_without_a_gsis_id_are_dropped_not_merged():
    # Finding 6: str(None) == "None" made every id-less row share one key.
    rows = [
        {"gsis_id": None, "full_name": "A One", "position": "WR", "team": "BAL", "status": "ACT", "week": 10},
        {"gsis_id": "", "full_name": "B Two", "position": "WR", "team": "BAL", "status": "ACT", "week": 10},
        {"gsis_id": "00-2", "full_name": "C Three", "position": "WR", "team": "BAL", "status": "ACT", "week": 10},
    ]
    assert [e.gsis_id for e in load_roster(2026, fetch=lambda season: rows)] == ["00-2"]


def test_a_roster_index_resolves_like_the_plain_roster(roster):
    # Finding 8: build the name index once per run rather than per INPUT.md line.
    from ffp.players import Roster, resolve_player

    index = Roster(roster)
    for text in ["Ja'Marr Chase", "AJ Brown", "Justin Jefferson", "Hollywood Brown"]:
        assert resolve_player(text, index) == resolve_player(text, roster)


def test_a_game_in_progress_is_found():
    # Finding 4: a Game that has kicked off but isn't scored is in progress, not "next week".
    from ffp.players import game_in_progress

    schedule = _schedule()
    during = datetime(2026, 11, 17, 2, 30, tzinfo=UTC)  # LAC @ BAL kicked off at 01:15Z
    assert game_in_progress("BAL", schedule, during).game_id == "2026_10_LAC_BAL"
    assert game_in_progress("BAL", schedule, datetime(2026, 11, 16, 12, tzinfo=UTC)) is None
    assert game_in_progress("BAL", schedule, datetime(2026, 11, 18, 12, tzinfo=UTC)) is None  # long over
