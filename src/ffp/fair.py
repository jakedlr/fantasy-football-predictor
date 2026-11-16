"""Prop Markets -> Fair Probabilities."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ffp.markets import PropMarket
from ffp.odds import american_to_implied, apply_logit_shift, calibrate_logit_shift


@dataclass(frozen=True)
class FairPoint:
    """A Fair Probability that Y >= threshold, with its weight in the Stat Distribution fit."""

    threshold: float
    prob: float
    weight: float


MAIN_LINE_WEIGHT = 3.0
RUNG_WEIGHT = 1.0
PROB_FLOOR = 1e-4  # Fair Probabilities are clipped into [PROB_FLOOR, 1 - PROB_FLOOR]

# Logit-shift vig c assumed for a book's Prop Market that has no Main Line to
# calibrate on. Derived from a typical −115/−115 prop Main Line:
#   q = 115/215 on each side, and by symmetry the fair p = 0.5, so
#   c = logit(q) − logit(0.5) = ln(q / (1 − q)) = ln(115/100) = ln 1.15 ≈ 0.1398.
# Override per book with fair_points(..., prior_c={"fanduel": ...}).
DEFAULT_PRIOR_C = math.log(1.15)


def fair_points(
    markets: Sequence[PropMarket], prior_c: Mapping[str, float] | None = None
) -> list[FairPoint]:
    """Fair Probabilities P(Y >= threshold) from one player's Prop Markets on one Scoring Stat.

    Each market (one per book) is devigged with the logit-shift model: c is
    calibrated on its Main Line, or taken from prior_c[book] (default
    DEFAULT_PRIOR_C) when it has none. Every Rung becomes a point of weight 1;
    the Main Line's fair Over becomes a point at floor(line) + 1 (stats are
    integers, so Y > 69.5 means Y >= 70) of weight 3. All books' points are
    merged by threshold and made non-increasing by weighted isotonic
    regression. Returned sorted by threshold.
    """
    prior_c = prior_c or {}
    points: list[FairPoint] = []
    for market in markets:
        points.extend(_market_points(market, prior_c.get(market.book, DEFAULT_PRIOR_C)))
    return _isotonic(_merge_thresholds(points))


def _market_points(market: PropMarket, prior_c: float) -> list[FairPoint]:
    """One book's devigged Rungs and Main Line, before merging and isotonic."""
    ml = market.main_line
    c = prior_c
    if ml is not None:
        q_over = american_to_implied(ml.over_odds)
        c = calibrate_logit_shift(q_over, american_to_implied(ml.under_odds))
    points = [
        FairPoint(rung.threshold, apply_logit_shift(american_to_implied(rung.odds), c), RUNG_WEIGHT)
        for rung in market.ladder
    ]
    if ml is not None:
        points.append(
            FairPoint(ml.over_threshold, apply_logit_shift(q_over, c), MAIN_LINE_WEIGHT)
        )
    return points


def _merge_thresholds(points: list[FairPoint]) -> list[FairPoint]:
    """One point per threshold, sorted: weighted-mean probability, summed weight."""
    merged: dict[float, tuple[float, float]] = {}
    for p in points:
        wp, w = merged.get(p.threshold, (0.0, 0.0))
        merged[p.threshold] = (wp + p.weight * p.prob, w + p.weight)
    return [FairPoint(t, wp / w, w) for t, (wp, w) in sorted(merged.items())]


def _isotonic(points: list[FairPoint]) -> list[FairPoint]:
    """Weighted pool-adjacent-violators: probabilities non-increasing in threshold.

    `points` must be sorted by threshold with distinct thresholds. Each output
    point keeps its own threshold and weight; violating neighbours share their
    pooled (weighted-mean) probability. Results are clipped into
    [PROB_FLOOR, 1 − PROB_FLOOR] so logits stay finite.
    """
    blocks: list[list[float]] = []  # [weighted prob sum, weight sum, point count]
    for p in points:
        blocks.append([p.weight * p.prob, p.weight, 1])
        # A violation is a later block with a higher mean than the one before it.
        while len(blocks) > 1 and blocks[-1][0] / blocks[-1][1] > blocks[-2][0] / blocks[-2][1]:
            wp, w, n = blocks.pop()
            blocks[-1][0] += wp
            blocks[-1][1] += w
            blocks[-1][2] += n
    probs = [wp / w for wp, w, n in blocks for _ in range(n)]
    return [
        FairPoint(p.threshold, min(max(prob, PROB_FLOOR), 1 - PROB_FLOOR), p.weight)
        for p, prob in zip(points, probs)
    ]
