import math

import pytest

from ffp.fair import fair_points
from ffp.markets import MainLine, PropMarket, Rung


def _logit(p):
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1 / (1 + math.exp(-x))


def american_for(q):
    """American odds whose implied probability is q (exact, not rounded)."""
    if q >= 0.5:
        return -100 * q / (1 - q)
    return 100 * (1 - q) / q


def vigged(p, c):
    """American odds a book with logit-shift vig c quotes for Fair Probability p."""
    return american_for(_sigmoid(_logit(p) + c))


def market(book, rungs, main=None, c=0.12):
    """A PropMarket from fair {threshold: p} and an optional fair Main Line (line, p_over)."""
    ladder = tuple(Rung(threshold=t, odds=vigged(p, c), book=book) for t, p in rungs.items())
    main_line = None
    if main is not None:
        line, p_over = main
        main_line = MainLine(
            line=line, over_odds=vigged(p_over, c), under_odds=vigged(1 - p_over, c), book=book
        )
    return PropMarket(
        player="Test Player",
        stat="receiving_yards",
        game_id="g1",
        book=book,
        main_line=main_line,
        ladder=ladder,
    )


def by_threshold(points):
    return {p.threshold: p for p in points}


def test_synthetic_ladder_with_known_vig_recovers_fair_probabilities():
    fair = {40: 0.88, 60: 0.68, 80: 0.40, 100: 0.18}
    m = market("draftkings", fair, main=(69.5, 0.55), c=0.15)

    points = by_threshold(fair_points([m]))

    for t, p in fair.items():
        assert points[t].prob == pytest.approx(p, abs=1e-9)
    assert points[70].prob == pytest.approx(0.55, abs=1e-9)


def test_main_line_point_has_weight_3_and_its_fair_over_probability():
    m = market("draftkings", {40: 0.9, 100: 0.2}, main=(52.5, 0.5), c=0.2)

    points = by_threshold(fair_points([m]))

    main = points[53]  # Over 52.5 means Y >= 53
    assert main.weight == 3
    assert main.prob == pytest.approx(0.5, abs=1e-9)
    assert points[40].weight == 1
    assert points[100].weight == 1


def test_without_main_line_uses_default_prior_c_from_a_minus_115_line():
    fair = {50: 0.7, 80: 0.3}
    m = market("fanduel", fair, main=None, c=math.log(1.15))

    points = by_threshold(fair_points([m]))

    assert points[50].prob == pytest.approx(0.7, abs=1e-9)
    assert points[80].prob == pytest.approx(0.3, abs=1e-9)


def test_output_is_non_increasing_even_with_a_crossed_rung():
    # 60+ priced below 80+: a stale or misquoted Rung.
    fair = {40: 0.9, 60: 0.35, 80: 0.45, 100: 0.2}
    m = market("draftkings", fair, main=(69.5, 0.5))

    points = fair_points([m])

    thresholds = [p.threshold for p in points]
    probs = [p.prob for p in points]
    assert thresholds == sorted(thresholds)
    assert all(a >= b for a, b in zip(probs, probs[1:]))


def test_isotonic_pools_violators_by_weight():
    # 60+ (w=1) at 0.35 and the Main Line 70 (w=3) at 0.5 cross: they pool to
    # (0.35 + 3 * 0.5) / 4 = 0.4625; 80+ at 0.47 then crosses that, so all
    # three pool to (0.35 + 1.5 + 0.47) / 5 = 0.464.
    fair = {40: 0.9, 60: 0.35, 80: 0.47, 100: 0.2}
    m = market("draftkings", fair, main=(69.5, 0.5))

    points = by_threshold(fair_points([m]))

    assert points[40].prob == pytest.approx(0.9)
    for t in (60, 70, 80):
        assert points[t].prob == pytest.approx(0.464)
    assert points[100].prob == pytest.approx(0.2)


def test_probabilities_are_clipped_away_from_0_and_1():
    m = market("draftkings", {5: 1 - 1e-7, 200: 1e-7}, main=(59.5, 0.5))

    points = by_threshold(fair_points([m]))

    assert points[5].prob == pytest.approx(1 - 1e-4)
    assert points[200].prob == pytest.approx(1e-4)


def test_prior_c_overrides_the_default_per_book():
    fair = {50: 0.7, 80: 0.3}
    m = market("fanduel", fair, main=None, c=0.3)

    points = by_threshold(fair_points([m], prior_c={"fanduel": 0.3}))

    assert points[50].prob == pytest.approx(0.7, abs=1e-9)
    assert points[80].prob == pytest.approx(0.3, abs=1e-9)
