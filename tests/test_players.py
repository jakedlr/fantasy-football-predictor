import csv
from pathlib import Path

import pytest

from ffp.players import (
    AmbiguousPlayerError,
    Player,
    PlayerError,
    RosterEntry,
    UnknownPlayerError,
    load_roster,
    resolve_player,
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


def test_player_errors_share_a_value_error_base():
    assert issubclass(UnknownPlayerError, PlayerError)
    assert issubclass(AmbiguousPlayerError, PlayerError)
    assert issubclass(PlayerError, ValueError)
