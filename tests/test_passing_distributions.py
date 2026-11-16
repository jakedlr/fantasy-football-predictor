"""QB passing Stat Distributions: passing yards, passing TDs and interceptions (S4.1)."""

import math

import numpy as np
import pytest
from scipy import stats
from scipy.special import expit

from ffp import params
from ffp.distributions import (
    PASS_TD_ATTEMPTS,
    Binomial,
    FitError,
    HurdleNormal,
    Poisson,
    fit_interceptions,
    fit_passing_tds,
    fit_passing_yards,
    passing_yards,
    passing_yards_pi0_relation,
    passing_yards_sd_relation,
)
from ffp.fair import FairPoint
from ffp.odds import american_to_implied, apply_logit_shift, calibrate_logit_shift

GRID = np.arange(-1000, 1500)  # m ± 10 sd and more: the normal has no lower bound
MUS = [180.0, 212.0, 240.0, 285.0]


def pmf(d, y):
    return d.sf(y) - d.sf(y + 1)


def relation():
    p = params.load("dispersion")["passing_yards"]["QB"]
    return p["pi0_c0"], p["pi0_c1"], p["sd_a"], p["sd_b"]


def pi0_rel(mu):
    c0, c1, _, _ = relation()
    return expit(c0 + c1 * math.log(mu))


def sd_rel(mu):
    _, _, a, b = relation()
    return a * (mu / (1 - pi0_rel(mu))) ** b


def fair_over(over, under):
    """The devigged Over of a two-way Main Line (logit shift, as ffp.fair does)."""
    q_over = american_to_implied(over)
    return apply_logit_shift(q_over, calibrate_logit_shift(q_over, american_to_implied(under)))


# --- passing yards: the distribution -------------------------------------------


@pytest.fixture(params=MUS)
def dist(request):
    mu = request.param
    return passing_yards(mu, sd_rel(mu))


def test_sf_is_non_increasing_and_spans_0_to_1(dist):
    s = dist.sf(GRID)
    assert np.all(np.diff(s) <= 1e-15)
    assert s[0] == pytest.approx(1.0)
    assert s[-1] == pytest.approx(0.0, abs=1e-12)


def test_sf_of_a_fractional_threshold_rounds_up(dist):
    assert dist.sf(np.array([200.5]))[0] == pytest.approx(dist.sf(np.array([201]))[0])


@pytest.mark.parametrize("mu", MUS)
def test_mean_is_the_requested_overall_mean(mu):
    d = passing_yards(mu, sd_rel(mu))
    assert d.mean == pytest.approx(mu, rel=1e-9)
    assert np.sum(GRID * pmf(d, GRID)) == pytest.approx(mu, rel=1e-9)


def test_point_mass_at_zero_is_pi0(dist):
    assert pmf(dist, np.array([0]))[0] == pytest.approx(dist.pi0, abs=1e-15)
    assert dist.pi0 == pytest.approx(pi0_rel(dist.mean))


def test_a_given_pi0_overrides_the_relation():
    d = passing_yards(230.0, 80.0, pi0=0.05)
    assert d.pi0 == 0.05
    assert pmf(d, np.array([0]))[0] == pytest.approx(0.05)
    assert d.mean == pytest.approx(230.0, rel=1e-9)


def test_location_solves_the_nonzero_mean():
    # A discretised normal's mean is m and the dropped y = 0 bin adds nothing to it, so the
    # zero-truncated nonzero mean is m / (1 - Z), Z the y = 0 bin's mass.
    d = passing_yards(240.0, 80.0)
    z = stats.norm.cdf(0.5, d.m, d.sd) - stats.norm.cdf(-0.5, d.m, d.sd)
    assert d.m / (1 - z) == pytest.approx(240.0 / (1 - d.pi0), rel=1e-11)
    assert d.sd == 80.0


def test_sd_relation_is_sd_a_times_the_nonzero_mean_to_the_b():
    mu = 240.0
    assert passing_yards_sd_relation(mu) == pytest.approx(sd_rel(mu))
    assert passing_yards_pi0_relation(mu) == pytest.approx(pi0_rel(mu))
    # Research Q4: sd 75-84 for starters.
    assert 70 < passing_yards_sd_relation(mu) < 90


def test_ppf_returns_integers_consistent_with_sf(dist):
    u = np.linspace(0.001, 0.999, 500)
    y = dist.ppf(u)
    assert np.issubdtype(y.dtype, np.integer)
    assert np.all(dist.sf(y + 1) <= 1 - u + 1e-12)
    assert np.all(dist.sf(y) > 1 - u)


def test_invalid_arguments_are_rejected():
    with pytest.raises(ValueError):
        passing_yards(240.0, 80.0, position="RB")
    with pytest.raises(ValueError):
        passing_yards(-1.0, 80.0)
    with pytest.raises(ValueError):
        passing_yards(240.0, 80.0, pi0=1.0)
    with pytest.raises(ValueError):
        HurdleNormal(240.0, 0.0, 0.0)


# --- passing yards: the fit ------------------------------------------------------


def ladder(dist, main_line):
    """The books' pass-yards Ladder: Rungs every 25 yards, plus the Main Line (weight 3)."""
    main = math.floor(main_line) + 1
    pts = [FairPoint(t, float(dist.sf(np.array([t]))[0]), 1.0) for t in range(150, 376, 25)]
    pts.append(FairPoint(main, float(dist.sf(np.array([main]))[0]), 3.0))
    return sorted(pts, key=lambda p: p.threshold)


@pytest.mark.parametrize("mu", [212.0, 245.0])
def test_fit_recovers_a_distribution_on_the_relation(mu):
    sd = sd_rel(mu)
    truth = passing_yards(mu, sd)
    fit = fit_passing_yards(ladder(truth, main_line=mu - 0.5))
    assert fit.mean == pytest.approx(mu, rel=1e-3)
    assert fit.sd == pytest.approx(sd, rel=1e-3)
    assert fit.pi0 == pytest.approx(truth.pi0, rel=1e-2)


def test_fit_recovers_a_player_wider_than_the_relation_within_prior_bias():
    # sd 1.15x the relation: the prior on ln sd pulls the fit back by a few % of ln 1.15.
    mu = 260.0
    sd = 1.15 * sd_rel(mu)
    truth = passing_yards(mu, sd)
    fit = fit_passing_yards(ladder(truth, main_line=259.5))
    assert fit.mean == pytest.approx(mu, abs=1.5)
    assert fit.sd == pytest.approx(sd, rel=0.03)


@pytest.mark.parametrize("line, over, under", [(200.5, -115, -115), (212.5, -110, -120)])
def test_main_line_only_fit_reproduces_the_main_line(line, over, under):
    # Herbert 200.5 and Brissett 212.5.
    p = fair_over(over, under)
    fit = fit_passing_yards([FairPoint(math.floor(line) + 1, p, 3.0)])
    assert fit.sf(np.array([line]))[0] == pytest.approx(p, abs=1e-9)
    assert fit.sd == pytest.approx(0.93 * passing_yards_sd_relation(fit.mean), rel=1e-9)
    assert fit.pi0 == pytest.approx(passing_yards_pi0_relation(fit.mean))
    assert line - 15 < fit.mean < line + 15


def test_fit_without_points_raises():
    with pytest.raises(FitError):
        fit_passing_yards([])


def test_fit_of_an_unreachable_main_line_raises():
    with pytest.raises(FitError):
        fit_passing_yards([FairPoint(900, 0.9, 3.0)])


# --- passing TDs ----------------------------------------------------------------


def test_binomial_is_a_count_with_mean_mu():
    d = Binomial(PASS_TD_ATTEMPTS, 1.6)
    k = np.arange(0, 13)
    assert d.mean == 1.6
    assert np.sum(k * pmf(d, k)) == pytest.approx(1.6)
    assert d.sf(np.array([1.5]))[0] == pytest.approx(1 - stats.binom.cdf(1, 11, 1.6 / 11))
    assert d.sf(np.array([0]))[0] == 1.0
    assert d.sf(np.array([12]))[0] == 0.0
    u = np.linspace(0.01, 0.99, 50)
    y = d.ppf(u)
    assert np.all(d.sf(y + 1) <= 1 - u + 1e-12)
    assert np.all(d.sf(y) > 1 - u)


def test_binomial_rejects_a_mean_outside_0_to_n():
    with pytest.raises(ValueError):
        Binomial(11, 11.5)
    with pytest.raises(ValueError):
        Binomial(11, -0.1)


def test_pass_tds_from_a_1_5_main_line():
    # O/U 1.5 at -150/+120: P(2+) is the devigged Over.
    p = fair_over(-150, +120)
    fit = fit_passing_tds([FairPoint(2, p, 3.0)])
    assert isinstance(fit, Binomial)
    assert fit.n == 11
    assert fit.sf(np.array([2]))[0] == pytest.approx(p, abs=1e-9)
    assert 1.5 < fit.mean < 2.5


def test_pass_tds_recovers_mu_from_a_1_2_3_ladder():
    truth = Binomial(11, 1.7)
    pts = [FairPoint(k, float(truth.sf(np.array([k]))[0]), 1.0) for k in (1, 2, 3)]
    assert fit_passing_tds(pts).mean == pytest.approx(1.7, rel=1e-6)


def test_pass_tds_p2_is_consistent_with_simulated_prices():
    # A QB at mu = 1.6. The O/U 1.5 is priced off the truth with -115/-105 vig and devigged;
    # the one-sided 1+/3+/4+ Rungs carry ~τ = 0.1 logit of leftover devig noise either way.
    # The Main Line (weight 3) dominates: the fitted P(2+) stays within 1pp of the truth (the
    # Rung noise moves it ~0.5pp here; the Main Line alone would move it 0.25pp).
    truth = Binomial(11, 1.6)

    def p(k):
        return float(truth.sf(np.array([k]))[0])

    def noisy(k, shift):
        return float(expit(math.log(p(k) / (1 - p(k))) + shift))

    main = FairPoint(2, noisy(2, 0.01), 3.0)  # what's left after devigging -115/-105
    rungs = [FairPoint(1, noisy(1, 0.1), 1.0), FairPoint(3, noisy(3, -0.1), 1.0), FairPoint(4, noisy(4, 0.1), 1.0)]
    fit = fit_passing_tds([*rungs, main])
    assert fit.sf(np.array([2]))[0] == pytest.approx(p(2), abs=0.01)
    assert fit.mean == pytest.approx(1.6, abs=0.05)


def test_pass_tds_contradictory_points_raise():
    # The Main Line says ~1 TD; the Rungs say a near-certain 8+.
    pts = [FairPoint(2, 0.3, 3.0), FairPoint(6, 0.99, 1.0), FairPoint(8, 0.99, 1.0), FairPoint(9, 0.99, 1.0)]
    with pytest.raises(FitError):
        fit_passing_tds(pts)


def test_pass_tds_running_to_the_bound_raises():
    with pytest.raises(FitError):
        fit_passing_tds([FairPoint(11, 0.9999, 1.0), FairPoint(10, 0.9999, 1.0)])


def test_pass_tds_without_points_raises():
    with pytest.raises(FitError):
        fit_passing_tds([])


# --- interceptions ---------------------------------------------------------------


def test_ints_lambda_from_a_0_5_line_is_minus_ln_p0():
    p = fair_over(+140, -175)  # O/U 0.5 INTs
    fit = fit_interceptions([FairPoint(1, p, 3.0)])
    assert isinstance(fit, Poisson)
    assert fit.lam == pytest.approx(-math.log(1 - p), rel=1e-9)


def test_ints_recovers_lambda_from_a_ladder():
    truth = Poisson(0.8)
    pts = [FairPoint(k, float(truth.sf(np.array([k]))[0]), w) for k, w in ((1, 3.0), (2, 1.0), (3, 1.0))]
    assert fit_interceptions(pts).lam == pytest.approx(0.8, rel=1e-6)


def test_ints_contradictory_points_raise():
    pts = [FairPoint(1, 0.3, 3.0), FairPoint(3, 0.99, 1.0), FairPoint(4, 0.99, 1.0), FairPoint(5, 0.99, 1.0)]
    with pytest.raises(FitError):
        fit_interceptions(pts)


def test_ints_without_points_raises():
    with pytest.raises(FitError):
        fit_interceptions([])
