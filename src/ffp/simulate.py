"""Monte Carlo: Stat Distributions x Scoring Rules -> Score Distribution -> Score Percentiles."""

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ffp.distributions import StatDistribution
from ffp.scoring import ScoringRules, score

DEFAULT_SEED = 20261115
DEFAULT_SAMPLES = 20_000
DEFAULT_PERCENTILES = (5, 25, 50, 75, 95)


def sample_stats(
    stat_dists: Mapping[str, StatDistribution],
    n: int,
    rng: np.random.Generator,
    uniforms: Mapping[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    """Draw n samples of each Scoring Stat by inverse CDF: ppf(u).

    Each stat gets its own independent uniforms from `rng`, unless `uniforms`
    supplies that stat's u values (length n), e.g. correlated ones from a copula.
    """
    uniforms = uniforms or {}
    samples = {}
    for stat, dist in stat_dists.items():
        u = uniforms[stat] if stat in uniforms else rng.uniform(size=n)
        samples[stat] = dist.ppf(u)
    return samples


@dataclass(frozen=True)
class ScoreDistribution:
    """A player's Score Distribution, held as Monte Carlo samples of fantasy points."""

    points: np.ndarray

    def percentile(self, k: float) -> float:
        """Score Percentile Pₖ: the smallest sample x with P(points <= x) >= k%.

        This is the inverse of the empirical CDF (numpy method "inverted_cdf",
        Hyndman & Fan type 1), so Pₖ is always an actual sampled score and no
        interpolation between samples happens.
        """
        return float(np.quantile(self.points, k / 100, method="inverted_cdf"))

    def percentiles(self, ks=DEFAULT_PERCENTILES) -> dict[int, float]:
        """Score Percentiles keyed by k."""
        return {k: self.percentile(k) for k in ks}

    def chance_of_at_least(self, points: float) -> float:
        """Chance of `points` or more: the share of samples >= points."""
        return float(np.mean(self.points >= points))

    @property
    def mean(self) -> float:
        return float(self.points.mean())


def simulate_scores(
    stat_dists: Mapping[str, StatDistribution],
    rules: ScoringRules,
    n: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> ScoreDistribution:
    """Sample every Stat Distribution independently and score each sample under `rules`.

    Stats without a Stat Distribution count as 0.
    """
    samples = sample_stats(stat_dists, n, np.random.default_rng(seed))
    return ScoreDistribution(np.asarray(score(samples, rules), dtype=float))
