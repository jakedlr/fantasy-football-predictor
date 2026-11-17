"""Backtest metrics: how well Stat Distributions and Score Distributions match outcomes.

Two kinds of forecast are scored:

- an integer-valued Stat Distribution, through its `sf(x) = P(Y >= x)`;
- a Score Distribution (or any continuous forecast), through Monte Carlo samples.

Under perfect calibration every PIT is uniform on (0, 1). CRPS, Brier score,
pinball loss and log loss are lower-is-better scores. Everything is numpy;
scipy is used only for the p-values of the uniformity tests.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats

from ffp.distributions import StatDistribution


def _sf(dist: StatDistribution, x) -> np.ndarray:
    return np.asarray(dist.sf(np.asarray(x)), dtype=float)


# --- PIT ---------------------------------------------------------------------


def randomized_pit(dist: StatDistribution, y: int, rng: np.random.Generator) -> float:
    """The randomized PIT of an integer outcome y: P(Y < y) + V·P(Y = y), V ~ U(0, 1).

    The randomization spreads the probability mass at y evenly, so the PIT is
    exactly uniform when the outcomes follow the distribution.
    """
    at_least_y, above_y = _sf(dist, [y, y + 1])
    return float((1 - at_least_y) + rng.uniform() * (at_least_y - above_y))


def pit_from_samples(samples: np.ndarray, y: float, rng: np.random.Generator) -> float:
    """The PIT of y against n Monte Carlo samples, ties broken at random.

    y's rank among the samples (#samples below y, plus a uniform draw from
    0..#samples equal to y) is uniform on 0..n under calibration; adding
    V ~ U(0, 1) and dividing by n + 1 makes the PIT continuous uniform on (0, 1).
    """
    samples = np.asarray(samples)
    below = int(np.count_nonzero(samples < y))
    ties = int(np.count_nonzero(samples == y))
    rank = below + rng.integers(0, ties + 1)
    return float((rank + rng.uniform()) / (len(samples) + 1))


@dataclass(frozen=True)
class PitHistogram:
    """PITs binned on [0, 1], with tests of uniformity (a small p-value flags miscalibration).

    A U shape means the forecasts are too narrow, a hump too wide, a slope biased.
    """

    counts: np.ndarray  # PITs per bin; 1.0 goes in the last bin
    edges: np.ndarray  # bins + 1 equally spaced edges from 0 to 1
    chi2: float  # Pearson chi-square against equal counts per bin
    chi2_p: float  # its p-value, bins - 1 degrees of freedom
    ks: float  # Kolmogorov-Smirnov distance from U(0, 1)
    ks_p: float
    n: int


def pit_histogram(pits: Sequence[float], bins: int = 10) -> PitHistogram:
    """Bin PITs into `bins` equal-width bins and test them for uniformity."""
    pits = np.asarray(pits, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    counts = np.bincount(_bin_index(pits, bins), minlength=bins)
    expected = len(pits) / bins
    chi2 = float(np.sum((counts - expected) ** 2) / expected)
    ks = stats.kstest(pits, "uniform")
    return PitHistogram(
        counts=counts,
        edges=edges,
        chi2=chi2,
        chi2_p=float(stats.chi2.sf(chi2, bins - 1)),
        ks=float(ks.statistic),
        ks_p=float(ks.pvalue),
        n=len(pits),
    )


def _bin_index(values: np.ndarray, bins: int) -> np.ndarray:
    """Equal-width bin of each value in [0, 1]; 1.0 goes in the last bin."""
    return np.clip(np.floor(values * bins).astype(int), 0, bins - 1)


# --- CRPS --------------------------------------------------------------------


def crps_from_samples(samples: np.ndarray, y: float) -> float:
    """CRPS of the samples' empirical distribution at y: E|X − y| − ½E|X − X'|.

    E|X − X'| over all n² ordered pairs (self-pairs included) equals
    (2/n²)·Σᵢ (2i − n − 1)·x₍ᵢ₎ for the sorted samples x₍₁₎ ≤ … ≤ x₍ₙ₎, i = 1..n,
    which costs a sort instead of n² differences.
    """
    x = np.sort(np.asarray(samples, dtype=float))
    n = len(x)
    i = np.arange(1, n + 1)
    mean_pair_gap = 2 * np.sum((2 * i - n - 1) * x) / n**2
    return float(np.mean(np.abs(x - y)) - 0.5 * mean_pair_gap)


def crps_integer(dist: StatDistribution, y: int, upper: int, lower: int = 0) -> float:
    """CRPS of an integer Stat Distribution at y: Σₖ (F(k) − 1{y ≤ k})².

    `lower` and `upper` bound the support: F(lower − 1) should be about 0 and
    F(upper) about 1 (e.g. lower = −20 for rushing yards). The sum runs over
    k = min(lower, y) .. max(upper, y), so an outcome outside the bounds still
    counts in full.
    """
    k = np.arange(min(lower, y), max(upper, y) + 1)
    cdf = 1 - _sf(dist, k + 1)  # F(k) = P(Y <= k)
    return float(np.sum((cdf - (y <= k)) ** 2))


# --- probabilities of events (P(≥k), Over lines) -----------------------------


@dataclass(frozen=True)
class Reliability:
    """A reliability diagram and the Brier score's Murphy decomposition.

    brier ≈ reliability − resolution + uncertainty (exact when the forecasts in
    each bin are equal). Lower reliability is better calibration; higher
    resolution is sharper. Empty bins have NaN mean_predicted and observed.
    """

    mean_predicted: np.ndarray  # mean forecast probability per bin
    observed: np.ndarray  # observed frequency of the event per bin
    counts: np.ndarray  # forecasts per bin
    brier: float
    reliability: float  # Σ nₖ (p̄ₖ − ōₖ)² / N
    resolution: float  # Σ nₖ (ōₖ − ō)² / N
    uncertainty: float  # ō (1 − ō)


def reliability(probs: np.ndarray, outcomes: np.ndarray, bins: int = 10) -> Reliability:
    """Bin forecast probabilities of an event into `bins` equal-width bins against 0/1 outcomes."""
    p = np.asarray(probs, dtype=float)
    o = np.asarray(outcomes, dtype=float)
    index = _bin_index(p, bins)
    counts = np.bincount(index, minlength=bins)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_predicted = np.bincount(index, weights=p, minlength=bins) / counts
        observed = np.bincount(index, weights=o, minlength=bins) / counts
    filled = counts > 0
    n = len(p)
    base_rate = o.mean()
    return Reliability(
        mean_predicted=mean_predicted,
        observed=observed,
        counts=counts,
        brier=float(np.mean((p - o) ** 2)),
        reliability=float(np.sum(counts[filled] * (mean_predicted[filled] - observed[filled]) ** 2) / n),
        resolution=float(np.sum(counts[filled] * (observed[filled] - base_rate) ** 2) / n),
        uncertainty=float(base_rate * (1 - base_rate)),
    )


def log_loss(p: np.ndarray, outcome: np.ndarray, eps: float = 1e-6) -> float:
    """Mean −[o·ln p + (1 − o)·ln(1 − p)], with p clipped to [eps, 1 − eps].

    The clip keeps a Monte Carlo probability of exactly 0 or 1 finite; about
    1/(2·samples) is a sensible eps for probabilities estimated from samples.
    """
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    o = np.asarray(outcome, dtype=float)
    return float(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p)))


# --- intervals and quantiles -------------------------------------------------


def coverage(lower: np.ndarray, upper: np.ndarray, y: np.ndarray) -> float:
    """Share of outcomes inside [lower, upper], ends included (P5–P95 should cover 90%)."""
    y = np.asarray(y)
    return float(np.mean((np.asarray(lower) <= y) & (y <= np.asarray(upper))))


def pinball_loss(q_pred: np.ndarray, y: np.ndarray, tau: float) -> float:
    """Mean quantile (pinball) loss of the predicted τ-quantiles: (y − q)(τ − 1{y < q})."""
    diff = np.asarray(y, dtype=float) - np.asarray(q_pred, dtype=float)
    return float(np.mean(diff * (tau - (diff < 0))))
