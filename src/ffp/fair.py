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


def fair_points(
    markets: Sequence[PropMarket], prior_c: Mapping[str, float] | None = None
) -> list[FairPoint]:
    points: list[FairPoint] = []
    for market in markets:
        ml = market.main_line
        q_over = american_to_implied(ml.over_odds)
        c = calibrate_logit_shift(q_over, american_to_implied(ml.under_odds))
        for rung in market.ladder:
            p = apply_logit_shift(american_to_implied(rung.odds), c)
            points.append(FairPoint(threshold=rung.threshold, prob=p, weight=RUNG_WEIGHT))
        points.append(
            FairPoint(
                threshold=math.floor(ml.line) + 1,
                prob=apply_logit_shift(q_over, c),
                weight=MAIN_LINE_WEIGHT,
            )
        )
    return sorted(points, key=lambda p: p.threshold)
