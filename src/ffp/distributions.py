"""Stat Distributions: one Scoring Stat for one player in one Game."""

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy import stats
from scipy.special import expit


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


class HurdleShiftedGamma:
    """Integer yards: a point mass pi0 at 0, else a discretised, zero-truncated shifted gamma.

    Construction (research/stat-distributions.md Q1a, Q4):
    - Continuous part X = G − shift, G ~ gamma with mean m + shift and sd `sd`,
      so X has mean m and sd `sd`.
    - Discretise: P(y) ∝ F(y + ½) − F(y − ½) for integer y, F the cdf of X.
      This is where the 0.5 continuity correction lives: P(Y >= x) for x >= 1 is
      the continuous tail beyond x − ½.
    - Zero-truncate: drop the bin y = 0 (mass Z = F(½) − F(−½)) and renormalise
      by 1 − Z. All of Y = 0 comes from the point mass pi0.
    - Negative yards: X >= −shift, so the bins y = −shift .. −1 keep the small mass
      the shifted gamma puts below −½ (real negative-yardage games). They are part
      of the nonzero distribution, so sf(x) = 1 for x <= −shift and the mean
      includes them. No floor is needed because nothing here takes a log-pmf.
    """

    def __init__(self, m: float, sd: float, pi0: float, shift: float):
        if m <= 0 or sd <= 0 or not 0 <= pi0 < 1:
            raise ValueError(f"invalid parameters m={m}, sd={sd}, pi0={pi0}")
        self.m, self.sd, self.pi0, self.shift = m, sd, pi0, shift
        g_mean = m + shift
        self._gamma = stats.gamma(a=(g_mean / sd) ** 2, scale=sd**2 / g_mean)
        self._zero_bin = self._gamma.cdf(shift + 0.5) - self._gamma.cdf(shift - 0.5)
        # Integer support: [lo, hi]; beyond hi the tail is below 1e-16.
        self._lo = -math.floor(shift)
        self._hi = math.ceil(self._gamma.isf(1e-16) - shift) + 1
        # Tail-sum formula for an integer variable: E[Y] = Σ_{x>=1} P(Y>=x) − Σ_{x<=0} P(Y<x).
        self._mean = float(
            np.sum(self.sf(np.arange(1, self._hi + 1))) - np.sum(1 - self.sf(np.arange(self._lo, 1)))
        )
        self._cdf = None  # P(Y <= y) on the support grid, built on first ppf call

    def sf(self, x: np.ndarray) -> np.ndarray:
        """P(Y >= x); a fractional x is rounded up (Y >= 69.5 means Y >= 70)."""
        x = np.ceil(np.asarray(x, dtype=float))
        tail = self._gamma.sf(x - 0.5 + self.shift)  # P(X >= x − ½)
        at_or_below_zero = x <= 0
        nz_sf = np.where(at_or_below_zero, tail - self._zero_bin, tail) / (1 - self._zero_bin)
        nz_sf = np.clip(nz_sf, 0.0, 1.0)
        return (1 - self.pi0) * nz_sf + self.pi0 * at_or_below_zero

    def ppf(self, u: np.ndarray) -> np.ndarray:
        """Smallest integer y with P(Y <= y) >= u."""
        ys = np.arange(self._lo, self._hi + 1)
        if self._cdf is None:
            self._cdf = 1 - self.sf(ys + 1)
        idx = np.searchsorted(self._cdf, np.asarray(u, dtype=float), side="left")
        return ys[np.minimum(idx, len(ys) - 1)]

    @property
    def mean(self) -> float:
        return self._mean


@dataclass(frozen=True)
class _ReceivingYardsParams:
    c0: float  # pi0 = expit(c0 + c1 ln mu), mu the overall mean
    c1: float
    a: float  # dispersion relation of the nonzero part: sd_rel(m) = a * m**b
    b: float


# research/stat-distributions.md Q2 two-part table / Q4 recommendation table.
RECEIVING_YARDS_PARAMS = {
    "WR": _ReceivingYardsParams(c0=6.75, c1=-2.48, a=3.00, b=0.622),
    "TE": _ReceivingYardsParams(c0=6.99, c1=-2.71, a=2.33, b=0.678),
}
RECEIVING_YARDS_SHIFT = 5.0


def _receiving_params(position: str) -> _ReceivingYardsParams:
    try:
        return RECEIVING_YARDS_PARAMS[position]
    except KeyError:
        raise ValueError(f"no receiving-yards model for position {position!r} (WR, TE)") from None


def receiving_yards(mu: float, sd: float, position: str) -> HurdleShiftedGamma:
    """Receiving-yards Stat Distribution for a WR or TE with overall mean mu.

    sd is the sd of the continuous nonzero part. Without a receptions model,
    pi0 = expit(c0 + c1 ln mu). The nonzero part must then have mean
    mu / (1 − pi0); the gamma's continuous mean m is solved so the discretised,
    zero-truncated nonzero mean hits that exactly, making the returned
    distribution's mean exactly mu.

    The location is mu rather than m because m(mu) = mu / (1 − pi0(mu)) is not
    monotone: for WRs it bottoms out near m ≈ 30 at mu ≈ 18, so an m below that
    has no consistent pi0 and an m above it has two.
    """
    p = _receiving_params(position)
    if mu <= 0 or sd <= 0:
        raise ValueError(f"invalid parameters mu={mu}, sd={sd}")
    pi0 = float(expit(p.c0 + p.c1 * math.log(mu)))
    target = mu / (1 - pi0)

    def nonzero_mean_error(m: float) -> float:
        return HurdleShiftedGamma(m, sd, 0.0, RECEIVING_YARDS_SHIFT).mean - target

    # Discretisation and zero-truncation move the mean by well under 1 yd, and
    # d(nonzero mean)/dm ≈ 1, so a Newton-like iteration m ← m − error converges fast.
    m = target
    for _ in range(100):
        err = nonzero_mean_error(m)
        if abs(err) < 1e-11 * target:
            return HurdleShiftedGamma(m, sd, pi0, RECEIVING_YARDS_SHIFT)
        m = max(m - err, m / 2)
    raise ValueError(f"nonzero mean did not converge for mu={mu}, sd={sd}")
