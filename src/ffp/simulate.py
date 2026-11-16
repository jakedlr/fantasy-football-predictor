"""Monte Carlo: Stat Distributions x Scoring Rules -> Score Distribution -> Score Percentiles."""

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from ffp.distributions import StatDistribution
from ffp.scoring import ScoringRules, score

DEFAULT_SEED = 20261115


def sample_stats(
    stat_dists: Mapping[str, StatDistribution],
    n: int,
    rng: np.random.Generator,
    uniforms: Mapping[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    return {stat: dist.ppf(rng.uniform(size=n)) for stat, dist in stat_dists.items()}


@dataclass(frozen=True)
class ScoreDistribution:
    points: np.ndarray

    def percentile(self, k: float) -> float:
        return float(np.quantile(self.points, k / 100, method="inverted_cdf"))

    def percentiles(self, ks=(5, 25, 50, 75, 95)) -> dict[int, float]:
        return {k: self.percentile(k) for k in ks}

    def chance_of_at_least(self, points: float) -> float:
        return float(np.mean(self.points >= points))

    @property
    def mean(self) -> float:
        return float(self.points.mean())


def simulate_scores(
    stat_dists: Mapping[str, StatDistribution],
    rules: ScoringRules,
    n: int = 20_000,
    seed: int = DEFAULT_SEED,
) -> ScoreDistribution:
    samples = sample_stats(stat_dists, n, np.random.default_rng(seed))
    return ScoreDistribution(np.asarray(score(samples, rules), dtype=float))
