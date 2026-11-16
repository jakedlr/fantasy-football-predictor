"""Monte Carlo: Stat Distributions x Scoring Rules -> Score Distribution -> Score Percentiles."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from ffp import copula
from ffp.distributions import StatDistribution, prob_negative, prob_zero
from ffp.scoring import ScoringRules, score

DEFAULT_SEED = 20261115
DEFAULT_SAMPLES = 20_000
DEFAULT_PERCENTILES = (5, 25, 50, 75, 95)

# (Scoring Stats in matrix order, latent correlation matrix), as params.correlation returns.
Correlation = tuple[Sequence[str], np.ndarray]
# Zeroed by the Rec = 0 rule.
RECEIVING_STATS = ("receiving_yards", "receiving_tds")


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


def zero_receiving_without_receptions(samples: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The structural rule (research/nflverse-data.md 5.3): no catch, no receiving yards or TDs.

    Where receptions are 0, receiving_yards and receiving_tds become 0. A no-op
    unless receptions were sampled. Returns `samples` with those arrays replaced.
    """
    if "receptions" not in samples:
        return samples
    no_catch = samples["receptions"] == 0
    for stat in RECEIVING_STATS:
        if stat in samples:
            samples[stat] = np.where(no_catch, 0, samples[stat])
    return samples


def sample_joint(
    stat_dists: Mapping[str, StatDistribution],
    n: int,
    rng: np.random.Generator,
    corr: Correlation | None = None,
) -> dict[str, np.ndarray]:
    """Draw n joint samples of the Scoring Stats.

    corr = (stats, R), e.g. `params.correlation(position, kind)`. The player's stats
    named in it get correlated uniforms from the Gaussian copula (the sub-matrix of
    R for those stats); any other stat (fumbles lost, 2-pt) is sampled independently.
    Then the Rec = 0 rule applies. With corr None this is exactly `sample_stats`.
    """
    if corr is None:
        return sample_stats(stat_dists, n, rng)
    stats_all = list(corr[0])
    matrix = copula.validate(stats_all, corr[1])
    names, sub = copula.submatrix(stats_all, matrix, stat_dists)
    # A sub-matrix of a validated matrix is valid: skip the second check (review finding 9).
    uniforms = copula.correlated_uniforms_unchecked(names, sub, n, rng) if names else {}
    samples = sample_stats(stat_dists, n, rng, uniforms)
    if "receptions" in samples:
        for stat in RECEIVING_STATS:
            if stat in uniforms:
                samples[stat] = _no_catch_coupled(
                    stat_dists[stat], uniforms[stat], samples["receptions"] == 0, stat_dists["receptions"]
                )
    return zero_receiving_without_receptions(samples)


def _no_catch_coupled(
    dist: StatDistribution, u: np.ndarray, no_catch: np.ndarray, receptions: StatDistribution
) -> np.ndarray:
    """Samples of a receiving stat that are 0 on no-catch games and keep the stat's own marginal.

    The stat's quantile scale [0, 1] has its zero mass at [q, q + p0], q = P(Y < 0) and
    p0 = P(Y = 0). The no-catch games (probability p_nc = P(receptions = 0)) are given the
    sub-interval [q, q + p_nc] of it, i.e. Y = 0. The catch games are re-ranked among
    themselves, w uniform on (0, 1) in the order of their copula draws (so the dependence
    on receptions is kept), and mapped to the rest of the scale: t = w (1 - p_nc), skipping
    the interval the no-catch games took. Overall, Y then has exactly its own distribution.
    That needs p0 >= p_nc; with less zero mass (a TD rate too high for this many no-catch
    games) the no-catch games still get 0, and the catch games take the rest of the scale.
    """
    p_nc = prob_zero(receptions)
    q, p0 = prob_negative(dist), prob_zero(dist)
    taken = min(p_nc, p0)  # the zero mass the no-catch games use
    catch = ~no_catch
    out = np.zeros_like(u, dtype=float)
    m = int(catch.sum())
    if m:
        ranks = np.empty(m)
        ranks[np.argsort(u[catch], kind="stable")] = np.arange(m)
        w = (ranks + 0.5) / m
        t = w * (1 - taken)
        v = np.where(t < q, t, t + taken)
        out[catch] = dist.ppf(np.clip(v, 1e-12, 1 - 1e-12))
    return out


def simulate_scores(
    stat_dists: Mapping[str, StatDistribution],
    rules: ScoringRules,
    n: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    corr: Correlation | None = None,
) -> ScoreDistribution:
    """Sample the Stat Distributions jointly (see `sample_joint`) and score each sample.

    Without `corr` every stat is sampled independently. Stats without a Stat
    Distribution count as 0.
    """
    samples = sample_joint(stat_dists, n, np.random.default_rng(seed), corr)
    return ScoreDistribution(np.asarray(score(samples, rules), dtype=float))
