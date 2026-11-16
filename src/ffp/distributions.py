"""Stat Distributions: one Scoring Stat for one player in one Game."""

from typing import Protocol

import numpy as np


class StatDistribution(Protocol):
    """Probability distribution of one Scoring Stat. Methods accept and return numpy arrays."""

    def sf(self, x: np.ndarray) -> np.ndarray:
        """P(Y >= x) for integer x."""

    def ppf(self, u: np.ndarray) -> np.ndarray:
        """Inverse CDF: the stat value at cumulative probability u in (0, 1).

        Used for sampling (u uniform) and, from Sprint 3, by the copula.
        """

    @property
    def mean(self) -> float:
        """Expected value."""
