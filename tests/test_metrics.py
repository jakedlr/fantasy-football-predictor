import numpy as np
import pytest
from scipy import stats

from ffp.metrics import (
    coverage,
    crps_from_samples,
    crps_integer,
    log_loss,
    pinball_loss,
    pit_from_samples,
    pit_histogram,
    randomized_pit,
    reliability,
)


class Discrete:
    """An integer Stat Distribution backed by a scipy discrete distribution: sf(x) = P(Y >= x)."""

    def __init__(self, frozen):
        self._d = frozen

    def sf(self, x):
        return self._d.sf(np.asarray(x) - 1)

    def ppf(self, u):
        return self._d.ppf(u)

    @property
    def mean(self):
        return float(self._d.mean())


def bernoulli(p):
    return Discrete(stats.bernoulli(p))


# --- randomized PIT ---------------------------------------------------------


def test_randomized_pit_lies_in_the_mass_of_the_observed_value():
    dist = Discrete(stats.poisson(2.0))
    lo, hi = stats.poisson.cdf(1, 2.0), stats.poisson.cdf(2, 2.0)  # P(Y < 2), P(Y <= 2)
    rng = np.random.default_rng(0)
    pits = [randomized_pit(dist, 2, rng) for _ in range(200)]
    assert lo <= min(pits) and max(pits) <= hi
    assert max(pits) - min(pits) > 0.9 * (hi - lo)  # V spreads it across the whole mass


def test_randomized_pit_of_a_bernoulli():
    # y = 0: u = 0 + V * (1 - p); y = 1: u = (1 - p) + V * p
    rng = np.random.default_rng(1)
    assert 0 <= randomized_pit(bernoulli(0.3), 0, rng) <= 0.7
    assert 0.7 <= randomized_pit(bernoulli(0.3), 1, rng) <= 1


def test_randomized_pit_is_uniform_when_outcomes_follow_the_distribution():
    rng = np.random.default_rng(2)
    dist = Discrete(stats.nbinom(3, 0.4))
    ys = stats.nbinom(3, 0.4).rvs(4000, random_state=rng)
    pits = np.array([randomized_pit(dist, y, rng) for y in ys])
    assert stats.kstest(pits, "uniform").pvalue > 0.01


def test_randomized_pit_flags_a_too_narrow_distribution():
    rng = np.random.default_rng(3)
    ys = stats.poisson(8).rvs(4000, random_state=rng)
    narrow = Discrete(stats.binom(16, 0.5))  # mean 8, variance 4 < 8
    pits = np.array([randomized_pit(narrow, y, rng) for y in ys])
    assert stats.kstest(pits, "uniform").pvalue < 1e-6


# --- PIT histogram ----------------------------------------------------------


def test_pit_histogram_counts_and_uniformity_statistics():
    pits = np.array([0.05, 0.15, 0.15, 0.95, 0.5, 0.55, 0.99, 0.0, 1.0, 0.42])
    h = pit_histogram(pits, bins=5)
    assert h.edges == pytest.approx([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    assert list(h.counts) == [4, 0, 3, 0, 3]  # 1.0 goes in the last bin
    expected = stats.chisquare(h.counts)  # against equal expected counts
    assert h.chi2 == pytest.approx(expected.statistic)
    assert h.chi2_p == pytest.approx(expected.pvalue)
    ks = stats.kstest(pits, "uniform")
    assert h.ks == pytest.approx(ks.statistic)
    assert h.ks_p == pytest.approx(ks.pvalue)
    assert h.n == 10


# --- PIT from Monte Carlo samples -------------------------------------------


def test_pit_from_samples_below_and_above_every_sample():
    samples = np.arange(1.0, 10.0)  # 9 distinct samples
    rng = np.random.default_rng(4)
    assert 0 <= pit_from_samples(samples, 0.5, rng) < 1 / 10
    assert 9 / 10 <= pit_from_samples(samples, 20.0, rng) <= 1


def test_pit_from_samples_randomizes_ties():
    samples = np.full(99, 5.0)  # a point mass: y = 5 ties every sample
    rng = np.random.default_rng(5)
    pits = np.array([pit_from_samples(samples, 5.0, rng) for _ in range(3000)])
    assert stats.kstest(pits, "uniform").pvalue > 0.01


def test_pit_from_samples_is_uniform_when_calibrated():
    rng = np.random.default_rng(6)
    pits = np.array([pit_from_samples(rng.gamma(3, 4, 200), rng.gamma(3, 4), rng) for _ in range(3000)])
    assert stats.kstest(pits, "uniform").pvalue > 0.01


# --- CRPS -------------------------------------------------------------------


def test_crps_from_samples_by_hand():
    # Samples {0, 2}, y = 1: E|X - y| = 1; E|X - X'| = (0 + 2 + 2 + 0) / 4 = 1; 1 - 1/2 = 0.5.
    # The integral: F = 0.5 on [0, 2), so the integrand is 0.25 on [0, 2) -> 0.5.
    assert crps_from_samples(np.array([2.0, 0.0]), 1.0) == pytest.approx(0.5)


def test_crps_from_samples_matches_the_pairwise_definition():
    rng = np.random.default_rng(7)
    x = rng.normal(10, 3, 300)
    y = 12.5
    brute = np.mean(np.abs(x - y)) - 0.5 * np.mean(np.abs(x[:, None] - x[None, :]))
    assert crps_from_samples(x, y) == pytest.approx(brute)


def test_crps_from_samples_approaches_the_normal_closed_form():
    # CRPS(N(0, 1), y) = y(2Φ(y) - 1) + 2φ(y) - 1/√π
    y = 0.7
    closed = y * (2 * stats.norm.cdf(y) - 1) + 2 * stats.norm.pdf(y) - 1 / np.sqrt(np.pi)
    x = np.random.default_rng(8).standard_normal(200_000)
    assert crps_from_samples(x, y) == pytest.approx(closed, abs=0.005)


def test_crps_from_samples_of_a_point_forecast_is_the_absolute_error():
    assert crps_from_samples(np.full(10, 3.0), 7.0) == pytest.approx(4.0)


def test_crps_integer_of_a_bernoulli_is_the_brier_score():
    # Σ_k (F(k) - 1{y <= k})²: y = 1 -> (1 - p)²; y = 0 -> p²
    assert crps_integer(bernoulli(0.3), 1, upper=1) == pytest.approx(0.49)
    assert crps_integer(bernoulli(0.3), 0, upper=1) == pytest.approx(0.09)


def test_crps_integer_matches_samples_of_the_same_distribution():
    dist = Discrete(stats.poisson(4.5))
    x = stats.poisson(4.5).rvs(200_000, random_state=np.random.default_rng(9))
    for y in (0, 4, 11):
        assert crps_integer(dist, y, upper=40) == pytest.approx(crps_from_samples(x, y), abs=0.01)


def test_crps_integer_extends_the_sum_to_an_outcome_outside_the_range():
    # A point mass at 0: CRPS = |y - 0|, even when y is past `upper` or below `lower`.
    point = Discrete(stats.randint(0, 1))
    assert crps_integer(point, 25, upper=10) == pytest.approx(25)
    assert crps_integer(point, -3, upper=10) == pytest.approx(3)


def test_crps_integer_with_negative_support():
    # Rushing yards can be negative: uniform on {-2, -1, 0, 1}, y = -1.
    # F(-2..1) = .25, .5, .75, 1; 1{y <= k} = 0, 1, 1, 1 -> .0625 + .25 + .0625 + 0 = 0.375
    dist = Discrete(stats.randint(-2, 2))
    assert crps_integer(dist, -1, upper=5, lower=-10) == pytest.approx(0.375)


# --- reliability and Brier --------------------------------------------------


def test_reliability_bins_and_brier_decomposition_by_hand():
    r = reliability(np.array([0.1, 0.1, 0.9, 0.9]), np.array([0, 1, 1, 1]), bins=10)
    assert list(r.counts) == [0, 2, 0, 0, 0, 0, 0, 0, 0, 2]
    assert r.mean_predicted[1] == pytest.approx(0.1) and r.observed[1] == pytest.approx(0.5)
    assert r.mean_predicted[9] == pytest.approx(0.9) and r.observed[9] == pytest.approx(1.0)
    assert np.isnan(r.mean_predicted[0]) and np.isnan(r.observed[0])
    # Brier: (.01 + .81 + .01 + .01) / 4 = .21
    assert r.brier == pytest.approx(0.21)
    # REL = (2 * .4² + 2 * .1²) / 4 = .085; RES = (2 * .25² + 2 * .25²) / 4 = .0625; UNC = .75 * .25
    assert r.reliability == pytest.approx(0.085)
    assert r.resolution == pytest.approx(0.0625)
    assert r.uncertainty == pytest.approx(0.1875)
    assert r.brier == pytest.approx(r.reliability - r.resolution + r.uncertainty)


def test_reliability_puts_probability_one_in_the_last_bin():
    r = reliability(np.array([0.0, 1.0]), np.array([0, 1]), bins=4)
    assert list(r.counts) == [1, 0, 0, 1]


# --- intervals, quantiles, log loss -----------------------------------------


def test_coverage_counts_inclusive_central_intervals():
    lower = np.array([0.0, 0.0, 0.0, 0.0])
    upper = np.array([10.0, 10.0, 10.0, 10.0])
    assert coverage(lower, upper, np.array([0.0, 5.0, 10.0, 10.5])) == pytest.approx(0.75)


def test_pinball_loss_by_hand():
    # tau .9, q 10: y 15 -> 5 * .9 = 4.5; y 5 -> (-5) * (.9 - 1) = 0.5; mean 2.5
    assert pinball_loss(np.array([10.0, 10.0]), np.array([15.0, 5.0]), 0.9) == pytest.approx(2.5)
    assert pinball_loss(10.0, 15.0, 0.1) == pytest.approx(0.5)


def test_log_loss_by_hand_and_clipped():
    # -(ln .8 + ln .75) / 2
    assert log_loss(np.array([0.8, 0.25]), np.array([1, 0])) == pytest.approx(-(np.log(0.8) + np.log(0.75)) / 2)
    # p = 0 for an outcome that happened is clipped to eps instead of infinite
    assert log_loss(np.array([0.0]), np.array([1]), eps=1e-4) == pytest.approx(-np.log(1e-4))
    assert log_loss(np.array([1.0]), np.array([0]), eps=1e-4) == pytest.approx(-np.log(1e-4))
