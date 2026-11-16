import csv
from pathlib import Path

import pytest

from ffp.players import (
    AmbiguousPlayerError,
    Player,
    PlayerError,
    RosterEntry,
    UnknownPlayerError,
    UnknownTeamError,
    UnsupportedPlayerError,
    load_roster,
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


def test_kicker_is_not_supported_yet(roster):
    with pytest.raises(UnsupportedPlayerError, match="not supported yet"):
        resolve_player("Evan McPherson", roster)


@pytest.mark.parametrize("text", [
    "Ravens D/ST", "BAL DST", "BAL D/ST", "Baltimore Ravens Defense", "Ravens", "Ravens DEF",
    "Ravens D-ST", "Bengals Defense", "Baltimore D/ST", "D/ST (BAL)",
])
def test_dst_is_not_supported_yet(roster, text):
    with pytest.raises(UnsupportedPlayerError, match="not supported yet"):
        resolve_player(text, roster)


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
