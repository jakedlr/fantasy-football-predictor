import pytest

from ffp.scoring import load_rules, score

# A WR stat line: 6 receptions, 85 receiving yards, 1 receiving TD, 1 fumble lost.
WR_LINE = {"receptions": 6, "receiving_yards": 85, "receiving_tds": 1, "fumbles_lost": 1}


def test_standard_preset_scores_wr_line():
    # 85 * 0.1 = 8.5 rec yds; 1 * 6 = 6 rec TD; 6 * 0 = 0 receptions; 1 * -2 = -2 fumble
    # 8.5 + 6 + 0 - 2 = 12.5
    assert score(WR_LINE, load_rules("standard")) == pytest.approx(12.5)
