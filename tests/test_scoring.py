from pathlib import Path

import pytest

from ffp.scoring import DEFAULT_RULES, load_rules, score

SCORING_DIR = Path(__file__).resolve().parents[1] / "scoring"

# A WR stat line: 6 receptions, 85 receiving yards, 1 receiving TD, 1 fumble lost.
WR_LINE = {"receptions": 6, "receiving_yards": 85, "receiving_tds": 1, "fumbles_lost": 1}


def test_standard_preset_scores_wr_line():
    # 85 * 0.1 = 8.5 rec yds; 1 * 6 = 6 rec TD; 6 * 0 = 0 receptions; 1 * -2 = -2 fumble
    # 8.5 + 6 + 0 - 2 = 12.5
    assert score(WR_LINE, load_rules("standard")) == pytest.approx(12.5)


@pytest.mark.parametrize(
    "preset, expected",
    [
        # 12.5 (standard total) + 6 * 0.5 receptions = 15.5
        ("half-ppr", 15.5),
        # 12.5 (standard total) + 6 * 1 receptions = 18.5
        ("ppr", 18.5),
    ],
)
def test_reception_presets_score_wr_line(preset, expected):
    assert score(WR_LINE, load_rules(preset)) == pytest.approx(expected)


def test_espn_league_scores_wr_line():
    # Full PPR: 6 * 1 receptions = 6; 85 * 0.1 = 8.5 rec yds; 1 * 6 = 6 rec TD; 1 * -2 = -2 fumble
    # 6 + 8.5 + 6 - 2 = 18.5
    rules = load_rules(DEFAULT_RULES, scoring_dir=SCORING_DIR)
    assert rules.name == "espn-league"
    assert score(WR_LINE, rules) == pytest.approx(18.5)
