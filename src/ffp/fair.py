"""Prop Markets -> Fair Probabilities."""

from dataclasses import dataclass


@dataclass(frozen=True)
class FairPoint:
    """A Fair Probability that Y >= threshold, with its weight in the Stat Distribution fit."""

    threshold: float
    prob: float
    weight: float
