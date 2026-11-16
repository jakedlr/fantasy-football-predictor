"""Receptions Stat Distribution (S3.1): negative binomial, fitted to Fair Probabilities."""

import numpy as np
import pytest
from scipy import stats

from ffp import params
from ffp.distributions import FitError, NegativeBinomial, fit_receiving_yards, fit_receptions, receiving_yards
from ffp.fair import FairPoint


def r_relation(mu, position):
    rel = params.load("dispersion")["receptions"][position]
    return rel["r_a"] * mu ** rel["r_b"]


# --- the family ---------------------------------------------------------------


@pytest.mark.parametrize("mu, r", [(4.0, 10.8), (1.2, 2.0), (6.5, 30.0)])
def test_negative_binomial_matches_scipy(mu, r):
    d = NegativeBinomial(mu, r)
    ref = stats.nbinom(r, r / (r + mu))
    x = np.arange(-2, 20)
    np.testing.assert_allclose(d.sf(x), np.where(x <= 0, 1.0, ref.sf(x - 1)), rtol=1e-12, atol=1e-15)
    assert d.mean == pytest.approx(mu)
    assert d.mu == mu and d.r == r


def test_negative_binomial_sf_of_a_fractional_threshold_rounds_up():
    d = NegativeBinomial(4.0, 10.0)
    assert d.sf(np.array([4.5]))[0] == d.sf(np.array([5]))[0]  # Over 4.5 is 5+


def test_negative_binomial_ppf_is_the_smallest_k_with_cdf_at_least_u():
    d = NegativeBinomial(4.0, 10.0)
    u = np.array([1e-6, 0.01, 0.3, 0.5, 0.77, 0.99, 1 - 1e-9])
    k = d.ppf(u)
    np.testing.assert_array_equal(k, np.round(k))  # integers
    cdf = lambda y: 1 - d.sf(y + 1)  # noqa: E731
    assert np.all(cdf(k) >= u - 1e-12)
    assert np.all((k == 0) | (cdf(k - 1) < u))


def test_negative_binomial_variance_is_mu_plus_mu_squared_over_r():
    d = NegativeBinomial(5.0, 16.4)
    y = np.arange(0, 80)
    pmf = d.sf(y) - d.sf(y + 1)
    assert np.sum(pmf * y) == pytest.approx(5.0, rel=1e-9)
    assert np.sum(pmf * (y - 5.0) ** 2) == pytest.approx(5.0 + 25.0 / 16.4, rel=1e-9)


@pytest.mark.parametrize("mu, r", [(0.0, 5.0), (-1.0, 5.0), (3.0, 0.0), (3.0, -2.0)])
def test_negative_binomial_rejects_invalid_parameters(mu, r):
    with pytest.raises(ValueError):
        NegativeBinomial(mu, r)


# --- fitting -------------------------------------------------------------------


def points_from(dist, rungs, main_threshold):
    pts = [FairPoint(t, float(dist.sf(np.array([t]))[0]), 1.0) for t in rungs]
    if main_threshold is not None:
        pts.append(FairPoint(main_threshold, float(dist.sf(np.array([main_threshold]))[0]), 3.0))
    return sorted(pts, key=lambda p: p.threshold)


@pytest.mark.parametrize("position, mu", [("WR", 5.2), ("TE", 3.6), ("WR", 2.4)])
def test_fit_recovers_a_known_negative_binomial_from_simulated_rungs(position, mu):
    # PLAN S3.1. Noise-free Rungs from an NB on the relation: the truth is the exact optimum.
    truth = NegativeBinomial(mu, r_relation(mu, position))
    pts = points_from(truth, [2, 3, 4, 6, 7, 8], main_threshold=5)
    fit = fit_receptions(pts, position)
    assert fit.mean == pytest.approx(mu, rel=1e-3)
    assert fit.r == pytest.approx(truth.r, rel=1e-3)


def test_fit_recovers_a_player_more_dispersed_than_the_relation():
    # r at half the relation (var/mean 1.5 instead of 1.25 at mu 5): a full Ladder pins it,
    # and the weak prior on ln r only shrinks it slightly toward the relation (measured: +2.8%).
    mu, position = 5.0, "WR"
    truth = NegativeBinomial(mu, 0.5 * r_relation(mu, position))
    pts = points_from(truth, [2, 3, 4, 5, 7, 8, 9, 10], main_threshold=6)
    fit = fit_receptions(pts, position)
    assert fit.mean == pytest.approx(mu, rel=0.01)
    assert fit.r == pytest.approx(truth.r, rel=0.05)
    assert truth.r < fit.r < r_relation(mu, position)  # shrunk toward the relation, not away


def test_with_fewer_than_three_points_besides_the_main_line_r_stays_on_the_relation():
    truth = NegativeBinomial(4.0, 0.5 * r_relation(4.0, "WR"))
    pts = points_from(truth, [3, 6], main_threshold=5)  # Main Line + 2 Rungs
    fit = fit_receptions(pts, "WR")
    assert fit.r == pytest.approx(r_relation(fit.mean, "WR"), rel=1e-9)
    assert fit.mean == pytest.approx(4.0, rel=0.1)


def test_a_ladder_without_main_line_needs_three_points_to_free_r():
    truth = NegativeBinomial(4.0, 0.5 * r_relation(4.0, "WR"))
    fixed = fit_receptions(points_from(truth, [3, 5], main_threshold=None), "WR")
    assert fixed.r == pytest.approx(r_relation(fixed.mean, "WR"), rel=1e-9)
    free = fit_receptions(points_from(truth, [3, 5, 7], main_threshold=None), "WR")
    assert free.r != pytest.approx(r_relation(free.mean, "WR"), rel=1e-3)


@pytest.mark.parametrize("position, threshold, prob", [("WR", 6, 0.53), ("TE", 1, 0.64), ("WR", 3, 0.47)])
def test_main_line_only_reproduces_the_main_line_exactly(position, threshold, prob):
    fit = fit_receptions([FairPoint(threshold, prob, 3.0)], position)
    assert fit.sf(np.array([threshold]))[0] == pytest.approx(prob, abs=1e-9)
    assert fit.r == pytest.approx(r_relation(fit.mean, position), rel=1e-9)


def test_rb_receptions_use_the_rb_relation():
    fit = fit_receptions([FairPoint(2, 0.45, 3.0)], "RB")
    assert fit.r == pytest.approx(r_relation(fit.mean, "RB"), rel=1e-9)


def test_fit_failures_are_fit_errors_but_an_unknown_position_is_not():
    with pytest.raises(FitError):
        fit_receptions([], "WR")
    with pytest.raises(FitError):
        fit_receptions([FairPoint(40, 0.9, 3.0)], "WR")  # out of the model's reach
    with pytest.raises(ValueError) as raised:
        fit_receptions([FairPoint(5, 0.5, 3.0)], "QB")
    assert not isinstance(raised.value, FitError)


def test_receptions_reject_an_unknown_position_before_fitting():
    with pytest.raises(ValueError, match="QB"):
        fit_receptions([], "QB")


# --- receiving yards' P(0) from receptions (research Q4: pi0 = P(rec = 0)) -------------


def test_receiving_yards_fit_with_a_given_pi0_keeps_it():
    truth = receiving_yards(14.0, 16.0, "WR", pi0=0.30)  # the formula would say ≈ 0.52
    xs = [5, 10, 15, 20, 30, 40]
    pts = [FairPoint(x, float(truth.sf(np.array([x]))[0]), 3.0 if x == 10 else 1.0) for x in xs]
    fit = fit_receiving_yards(pts, "WR", pi0=0.30)
    assert fit.pi0 == 0.30
    assert fit.mean == pytest.approx(14.0, rel=0.01)
    assert fit.sd == pytest.approx(16.0, rel=0.05)


def test_receiving_yards_main_line_only_with_a_given_pi0_reproduces_the_main_line():
    from ffp.distributions import MAIN_LINE_ONLY_KAPPA, receiving_yards_sd_relation

    fit = fit_receiving_yards([FairPoint(53, 0.47, 3.0)], "WR", pi0=0.08)
    assert fit.pi0 == 0.08
    assert fit.sf(np.array([53]))[0] == pytest.approx(0.47, abs=1e-9)
    assert fit.sd == pytest.approx(
        MAIN_LINE_ONLY_KAPPA * receiving_yards_sd_relation(fit.mean, "WR", 0.08), rel=1e-9
    )


def test_receiving_yards_pi0_from_the_receptions_fit():
    # How the integrator links them: P(0 yards) = P(0 receptions) = 1 − P(rec >= 1).
    rec = fit_receptions([FairPoint(1, 0.64, 3.0), FairPoint(2, 0.30, 1.0)], "TE")
    pi0 = 1 - float(rec.sf(np.array([1]))[0])
    yards = fit_receiving_yards([FairPoint(10, 0.45, 3.0), FairPoint(20, 0.2, 1.0)], "TE", pi0=pi0)
    p_zero_yards = yards.sf(np.array([0]))[0] - yards.sf(np.array([1]))[0]
    p_zero_catches = 1 - rec.sf(np.array([1]))[0]
    assert p_zero_yards == pytest.approx(p_zero_catches, abs=1e-12)


@pytest.mark.parametrize("pi0", [-0.1, 1.0, 1.5])
def test_receiving_yards_reject_an_impossible_pi0(pi0):
    with pytest.raises(ValueError):
        fit_receiving_yards([FairPoint(53, 0.47, 3.0)], "WR", pi0=pi0)


@pytest.mark.parametrize("ps", [[0.03, 0.02, 0.01, 0.005], [1e-3, 5e-4, 2e-4, 1e-4], [0.9999] * 4])
def test_extreme_probabilities_fit_or_fail_cleanly(ps):
    pts = [FairPoint(x, p, 3.0 if i == 0 else 1.0) for i, (x, p) in enumerate(zip([2, 4, 6, 8], ps))]
    try:
        fit = fit_receptions(pts, "WR")
    except FitError:
        return
    assert fit.mean > 0 and fit.r > 0
