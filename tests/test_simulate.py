from pathlib import Path

import numpy as np
import pytest

from ffp.scoring import load_rules
from ffp.simulate import DEFAULT_SEED, ScoreDistribution, sample_stats, simulate_scores


class DiscreteDist:
    """Test-double Stat Distribution over explicit integer values with an explicit pmf."""

    def __init__(self, values, pmf):
        self.values = np.asarray(values, dtype=float)
        self.pmf = np.asarray(pmf, dtype=float)
        self.cdf = np.cumsum(self.pmf)

    def sf(self, x):
        x = np.asarray(x)
        return np.array([self.pmf[self.values >= xi].sum() for xi in np.ravel(x)]).reshape(x.shape)

    def ppf(self, u):
        idx = np.searchsorted(self.cdf, np.asarray(u), side="left")
        return self.values[np.minimum(idx, len(self.values) - 1)]

    @property
    def mean(self):
        return float((self.values * self.pmf).sum())


# Receiving yards: 0 with 10%, then a spread of values up to 150.
REC_YDS = DiscreteDist([0, 20, 40, 60, 80, 100, 150], [0.1, 0.1, 0.2, 0.2, 0.2, 0.1, 0.1])
RECEPTIONS = DiscreteDist([0, 2, 4, 6, 8], [0.1, 0.2, 0.3, 0.3, 0.1])

ESPN_FILE = Path(__file__).resolve().parents[1] / "src" / "ffp" / "rules" / "espn-league.json"
ESPN = load_rules(ESPN_FILE)  # the bundled file by path, so a local scoring/ copy can't change it


def test_same_seed_gives_identical_percentiles():
    dists = {"receiving_yards": REC_YDS, "receptions": RECEPTIONS}
    a = simulate_scores(dists, ESPN, n=5_000, seed=123)
    b = simulate_scores(dists, ESPN, n=5_000, seed=123)
    assert a.percentiles() == b.percentiles()


def test_percentiles_are_ordered():
    dists = {"receiving_yards": REC_YDS, "receptions": RECEPTIONS}
    p = simulate_scores(dists, ESPN, n=5_000, seed=7).percentiles()
    assert list(p) == [5, 25, 50, 75, 95]
    assert p[5] <= p[25] <= p[50] <= p[75] <= p[95]
    assert p[5] < p[95]


def test_receiving_yards_only_scores_a_tenth_of_a_point_per_yard_under_espn():
    dists = {"receiving_yards": REC_YDS}
    samples = sample_stats(dists, 2_000, np.random.default_rng(99))
    result = simulate_scores(dists, ESPN, n=2_000, seed=99)
    np.testing.assert_allclose(result.points, 0.1 * samples["receiving_yards"])


class PointMass:
    """Test-double Stat Distribution that always takes one value."""

    def __init__(self, value):
        self.value = float(value)

    def sf(self, x):
        return np.where(np.asarray(x) <= self.value, 1.0, 0.0)

    def ppf(self, u):
        return np.full(np.shape(u), self.value)

    @property
    def mean(self):
        return self.value


def test_point_mass_gives_equal_percentiles_at_that_score():
    # 7 receptions * 1 + 85 yards * 0.1 + 1 TD * 6 = 7 + 8.5 + 6 = 21.5
    dists = {"receptions": PointMass(7), "receiving_yards": PointMass(85), "receiving_tds": PointMass(1)}
    result = simulate_scores(dists, ESPN, n=1_000)
    for value in result.percentiles().values():
        assert value == pytest.approx(21.5)
    assert result.mean == pytest.approx(21.5)


def test_chance_of_at_least_matches_the_samples():
    result = simulate_scores({"receiving_yards": REC_YDS}, ESPN, n=5_000, seed=3)
    for points in (0, 4, 6, 8, 10, 15, 100):
        assert result.chance_of_at_least(points) == pytest.approx(np.mean(result.points >= points))
    # Points take values 0, 2, 4, ..., 15 exactly; P(points >= 8) is P(yards >= 80) = 0.4.
    assert result.chance_of_at_least(8) == pytest.approx(0.4, abs=0.03)


def test_percentile_k_has_k_percent_at_or_below_it():
    # Pk is the smallest sample x with P(points <= x) >= k%.
    dist = ScoreDistribution(np.arange(1.0, 21.0))  # 1..20, each 5%
    assert dist.percentile(5) == 1.0
    assert dist.percentile(25) == 5.0
    assert dist.percentile(50) == 10.0
    assert dist.percentile(95) == 19.0


def test_injected_uniforms_are_used():
    u = np.array([0.05, 0.15, 0.95, 0.5])
    # REC_YDS cdf: 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0 -> u maps to 0, 20, 150, 60.
    samples = sample_stats(
        {"receiving_yards": REC_YDS, "receptions": RECEPTIONS},
        4,
        np.random.default_rng(0),
        uniforms={"receiving_yards": u},
    )
    np.testing.assert_array_equal(samples["receiving_yards"], [0, 20, 150, 60])
    assert samples["receptions"].shape == (4,)


def test_defaults_are_20000_samples_and_a_fixed_seed():
    dists = {"receiving_yards": REC_YDS}
    default = simulate_scores(dists, ESPN)
    assert default.points.shape == (20_000,)
    np.testing.assert_array_equal(default.points, simulate_scores(dists, ESPN, n=20_000, seed=DEFAULT_SEED).points)
