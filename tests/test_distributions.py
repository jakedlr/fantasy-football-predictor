import math

import numpy as np
import pytest
from scipy.special import expit

from ffp.distributions import receiving_yards

GRID = np.arange(-10, 1500)


# (position, overall mean mu, sd of the nonzero part): research Q2 examples, plus a low mean.
CASES = [("WR", 60.0, 39.1), ("WR", 30.0, 27.7), ("TE", 40.0, 29.4), ("WR", 12.0, 15.0)]


@pytest.fixture(params=CASES)
def dist(request):
    position, mu, sd = request.param
    return receiving_yards(mu, sd, position)


def pmf(d, y):
    return d.sf(y) - d.sf(y + 1)


def test_sf_is_non_increasing_and_spans_0_to_1(dist):
    s = dist.sf(GRID)
    assert np.all(np.diff(s) <= 0)
    assert s[0] == pytest.approx(1.0)
    assert s[-1] == pytest.approx(0.0, abs=1e-12)


def test_sf_of_a_fractional_threshold_rounds_up():
    d = receiving_yards(60.0, 39.1, "WR")
    assert d.sf(np.array([69.5]))[0] == pytest.approx(d.sf(np.array([70]))[0])


def test_mean_matches_a_numeric_sum(dist):
    assert dist.mean == pytest.approx(np.sum(GRID * pmf(dist, GRID)), rel=1e-9)


@pytest.mark.parametrize("position, mu, sd", CASES)
def test_mean_is_the_requested_overall_mean(position, mu, sd):
    assert receiving_yards(mu, sd, position).mean == pytest.approx(mu, rel=1e-9)


def test_point_mass_at_zero_is_consistent_with_the_overall_mean():
    # pi0 = expit(c0 + c1 ln mu) with mu the distribution's own mean (Q4 table).
    wr = receiving_yards(60.0, 39.1, "WR")
    te = receiving_yards(40.0, 29.4, "TE")
    assert pmf(wr, np.array([0]))[0] == pytest.approx(expit(6.75 - 2.48 * math.log(wr.mean)))
    assert pmf(te, np.array([0]))[0] == pytest.approx(expit(6.99 - 2.71 * math.log(te.mean)))


def test_research_example_wr_mu_60():
    # stat-distributions.md Q2: WR mu = 60 -> (pi0 .033, m 62.0, sd_nz 39.1).
    # m is the continuous nonzero mean; discretisation and zero-truncation move
    # the realised nonzero mean by ~0.06 yd here, so m sits just below 62.0.
    d = receiving_yards(60.0, 39.1, "WR")
    assert d.m == pytest.approx(62.0, abs=0.1)
    assert pmf(d, np.array([0]))[0] == pytest.approx(0.033, abs=0.002)


def test_ppf_returns_integers_consistent_with_sf(dist):
    u = np.linspace(0.001, 0.999, 500)
    y = dist.ppf(u)
    assert np.issubdtype(y.dtype, np.integer)
    # y is the smallest integer with P(Y <= y) >= u, i.e. sf(y + 1) <= 1 - u < sf(y).
    assert np.all(dist.sf(y + 1) <= 1 - u + 1e-12)
    assert np.all(dist.sf(y) > 1 - u)


def test_ppf_of_mid_quantile_is_zero_inside_the_point_mass():
    d = receiving_yards(12.0, 15.0, "WR")  # pi0 is large (~0.66) at low means
    below_zero = 1 - d.sf(np.array([0]))[0]
    pi0 = pmf(d, np.array([0]))[0]
    assert d.ppf(np.array([below_zero + pi0 / 2]))[0] == 0


def test_other_positions_are_rejected():
    with pytest.raises(ValueError):
        receiving_yards(30.0, 20.0, "RB")
