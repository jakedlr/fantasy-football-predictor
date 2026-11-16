import math

import numpy as np
import pytest
from scipy.special import expit

from ffp.distributions import fit_receiving_yards, receiving_yards
from ffp.fair import FairPoint

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


# --- fitting -----------------------------------------------------------------

RELATION = {"WR": (6.75, -2.48, 3.00, 0.622), "TE": (6.99, -2.71, 2.33, 0.678)}


def sd_relation(mu, position):
    """research Q2/Q4: sd_nz = a * m**b with m = mu / (1 - pi0(mu))."""
    c0, c1, a, b = RELATION[position]
    m = mu / (1 - expit(c0 + c1 * math.log(mu)))
    return a * m**b


def points_from(dist, rungs, main_threshold):
    pts = [FairPoint(t, float(dist.sf(np.array([t]))[0]), 1.0) for t in rungs]
    pts.append(FairPoint(main_threshold, float(dist.sf(np.array([main_threshold]))[0]), 3.0))
    return sorted(pts, key=lambda p: p.threshold)


@pytest.mark.parametrize("position, mu", [("WR", 60.0), ("TE", 40.0)])
def test_fit_recovers_a_distribution_on_the_relation(position, mu):
    # Noise-free points from a player who follows the dispersion relation: the
    # residuals and the prior are both zero at the truth, so it is the exact
    # optimum and only optimiser tolerance separates the fit from it.
    sd = sd_relation(mu, position)
    truth = receiving_yards(mu, sd, position)
    pts = points_from(truth, [25, 40, 60, 80, 100, 125], main_threshold=round(mu) - 6)

    fit = fit_receiving_yards(pts, position)

    assert fit.mean == pytest.approx(mu, rel=1e-3)
    assert fit.sd == pytest.approx(sd, rel=1e-3)


def test_fit_recovers_a_player_wider_than_the_relation_within_prior_bias():
    # Truth sd is 1.2x the relation (a ~2-sigma departure per research Q2). The
    # prior lambda (ln sd - ln sd_rel)^2 pulls ln sd toward the relation by about
    # lambda / (lambda + H) * ln 1.2, where H = sum w_i (d logit S_i / d ln sd)^2.
    # With 6 Rungs + Main Line H is ~5-10 against lambda = 0.44, so the expected
    # shrinkage is ~1.5-3% of ln 1.2, i.e. ~1% of sd (measured: 0.9%, mean off by
    # 0.07 yd). A 3% tolerance on sd and 1 yd on the mean allow for that bias
    # and nothing more (the free fit would recover the truth exactly).
    mu, position = 60.0, "WR"
    sd = 1.2 * sd_relation(mu, position)
    truth = receiving_yards(mu, sd, position)
    pts = points_from(truth, [25, 40, 60, 80, 100, 125], main_threshold=53)

    fit = fit_receiving_yards(pts, position)

    assert fit.mean == pytest.approx(mu, abs=1.0)
    assert fit.sd == pytest.approx(sd, rel=0.03)
    assert fit.sd < sd  # shrunk toward the relation, not away from it


def test_main_line_only_reproduces_the_main_line_probability_exactly():
    pts = [FairPoint(53, 0.47, 3.0)]  # e.g. Over 52.5 devigged to 0.47

    fit = fit_receiving_yards(pts, "WR")

    assert fit.sf(np.array([53]))[0] == pytest.approx(0.47, abs=1e-9)
    assert fit.sd == pytest.approx(0.93 * sd_relation(fit.mean, "WR"), rel=1e-9)


def test_fit_rejects_other_positions():
    with pytest.raises(ValueError):
        fit_receiving_yards([FairPoint(30, 0.5, 3.0)], "RB")


def test_fit_rejects_no_points():
    with pytest.raises(ValueError):
        fit_receiving_yards([], "WR")


def test_fit_failures_are_fit_errors_but_an_unknown_position_is_not():
    # predict skips a player only on FitError, so a bad position must not be one.
    from ffp.distributions import FitError

    with pytest.raises(FitError):
        fit_receiving_yards([], "WR")
    with pytest.raises(FitError):
        fit_receiving_yards([FairPoint(400, 0.999, 3.0)], "WR")  # out of reach for mu <= 500
    with pytest.raises(ValueError) as raised:
        fit_receiving_yards([FairPoint(30, 0.5, 3.0)], "RB")
    assert not isinstance(raised.value, FitError)
