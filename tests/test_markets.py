import pytest

from ffp.markets import MainLine


@pytest.mark.parametrize("line, threshold", [(71.5, 72), (5.5, 6), (0.5, 1), (37.5, 38)])
def test_over_a_half_point_line_is_y_at_least_the_next_integer(line, threshold):
    assert MainLine(line, -110, -110, "draftkings").over_threshold == threshold
