import csv
from pathlib import Path

import pytest

from ffp.outcomes import OUTCOME_STATS, fantasy_points, load_outcomes
from ffp.scoring import SCORING_STATS, load_rules

# Five real nflverse load_player_stats(2026, summary_level="week") rows, trimmed to the
# columns load_outcomes reads plus fumbles_lost_total and fantasy_points_ppr.
FIXTURE = Path(__file__).resolve().parents[1] / "research" / "fixtures" / "nflverse_player_stats_2026.csv"


def fixture_fetch(season):
    """Rows as a CSV gives them: every value a string, like the other nflverse fixtures."""
    with FIXTURE.open() as f:
        return [row for row in csv.DictReader(f) if int(row["season"]) == season]


@pytest.fixture
def outcomes():
    return load_outcomes([2026], fetch=fixture_fetch)


def test_outcomes_are_keyed_by_game_id_and_gsis_id(outcomes):
    assert set(outcomes) == {
        ("2026_05_CIN_MIA", "00-0038128"),  # Malik Willis
        ("2026_05_BUF_LA", "00-0034857"),  # Josh Allen
        ("2026_05_BUF_LA", "00-0037248"),  # James Cook
        ("2026_05_SF_SEA", "00-0031408"),  # Mike Evans
        ("2026_01_CHI_CAR", "00-0040218"),  # Jimmy Horn Jr.
    }


def test_each_outcome_has_every_scoring_stat_and_carries(outcomes):
    assert OUTCOME_STATS == (*SCORING_STATS, "carries")
    for stats in outcomes.values():
        assert set(stats) == set(OUTCOME_STATS)


def test_columns_map_onto_scoring_stats(outcomes):
    # Malik Willis, 2026 week 5: 137 pass yds, 1 pass TD, 2 INT, 1 pass 2-pt,
    # 8 carries for 74 yds and 1 TD, 1 sack fumble lost.
    assert outcomes["2026_05_CIN_MIA", "00-0038128"] == {
        "passing_yards": 137,
        "passing_tds": 1,
        "passing_interceptions": 2,
        "passing_2pt": 1,
        "rushing_yards": 74,
        "rushing_tds": 1,
        "rushing_2pt": 0,
        "receptions": 0,
        "receiving_yards": 0,
        "receiving_tds": 0,
        "receiving_2pt": 0,
        "fumbles_lost": 1,
        "carries": 8,
    }
    # Mike Evans: a receiving 2-pt conversion.
    assert outcomes["2026_05_SF_SEA", "00-0031408"]["receiving_2pt"] == 1


def test_fumbles_lost_sums_sack_rushing_and_receiving_not_the_total(outcomes):
    # Jimmy Horn Jr. lost a fumble on a return: fumbles_lost_total is 1 but the
    # three per-play-type columns are 0, and Sleeper doesn't charge it.
    assert outcomes["2026_01_CHI_CAR", "00-0040218"]["fumbles_lost"] == 0
    # James Cook's lost fumble was on a carry.
    assert outcomes["2026_05_BUF_LA", "00-0037248"]["fumbles_lost"] == 1


@pytest.mark.parametrize(
    "key, sleeper_points",
    [
        # Exact Sleeper totals Kalshi's KXNFLFFPTSLADDER published as expiration_value
        # (research/kalshi-fantasy-points.md; data/kalshi/research/ffpts_markets.json).
        (("2026_05_CIN_MIA", "00-0038128"), 20.88),  # Willis: INT, sack fumble lost, pass 2-pt
        (("2026_05_BUF_LA", "00-0034857"), 31.5),  # Allen: INT, pass 2-pt
        (("2026_05_BUF_LA", "00-0037248"), 22.8),  # Cook: rushing fumble lost
        (("2026_05_SF_SEA", "00-0031408"), 26.3),  # Evans: receiving 2-pt
    ],
)
def test_fantasy_points_reproduce_sleeper_totals(outcomes, key, sleeper_points):
    assert fantasy_points(outcomes[key], load_rules("sleeper")) == sleeper_points


def test_fantasy_points_are_rounded_to_two_decimals():
    # 0.04 * 137 is 5.4799999... in floating point; Sleeper publishes 2 decimals,
    # and an exact total matters against a strictly-greater line like 5.48.
    assert fantasy_points({"passing_yards": 137}, load_rules("sleeper")) == 5.48


def test_missing_values_count_as_zero():
    row = {"player_id": "00-1", "game_id": "2026_01_A_B", "passing_yards": None, "carries": ""}
    stats = load_outcomes([2026], fetch=lambda season: [row])["2026_01_A_B", "00-1"]
    assert stats == dict.fromkeys(OUTCOME_STATS, 0)


def test_rows_without_a_player_or_game_id_are_skipped():
    rows = [{"player_id": None, "game_id": "2026_01_A_B"}, {"player_id": "00-1", "game_id": ""}]
    assert load_outcomes([2026], fetch=lambda season: rows) == {}


def test_several_seasons_are_fetched_one_by_one():
    fetched = []

    def fetch(season):
        fetched.append(season)
        return [{"player_id": "00-1", "game_id": f"{season}_01_A_B", "receptions": "3"}]

    outcomes = load_outcomes([2025, 2026], fetch=fetch)
    assert fetched == [2025, 2026]
    assert outcomes["2025_01_A_B", "00-1"]["receptions"] == 3
    assert ("2026_01_A_B", "00-1") in outcomes


def test_a_single_season_may_be_an_int():
    outcomes = load_outcomes(2026, fetch=fixture_fetch)
    assert len(outcomes) == 5
