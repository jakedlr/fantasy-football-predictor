import csv
from pathlib import Path

import pytest

from ffp.players import Player, load_roster, resolve_player

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
