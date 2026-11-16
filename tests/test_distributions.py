import math

import numpy as np
import pytest
from scipy.special import expit

from ffp import params
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
    assert pmf(wr, np.array([0]))[0] == pytest.approx(pi0_relation(wr.mean, "WR"))
    assert pmf(te, np.array([0]))[0] == pytest.approx(pi0_relation(te.mean, "TE"))


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
        receiving_yards(30.0, 20.0, "QB")


# --- fitting -----------------------------------------------------------------

def relation(position):
    """The receiving-yards relations from the committed params (scripts/build_params.py)."""
    p = params.load("dispersion")["receiving_yards"][position]
    return p["pi0_c0"], p["pi0_c1"], p["sd_a"], p["sd_b"]


def pi0_relation(mu, position):
    c0, c1, _, _ = relation(position)
    return expit(c0 + c1 * math.log(mu))


def sd_relation(mu, position):
    """research Q2/Q4: sd_nz = a * m**b with m = mu / (1 - pi0(mu))."""
    _, _, a, b = relation(position)
    m = mu / (1 - pi0_relation(mu, position))
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
        fit_receiving_yards([FairPoint(30, 0.5, 3.0)], "QB")


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
        fit_receiving_yards([FairPoint(30, 0.5, 3.0)], "QB")
    assert not isinstance(raised.value, FitError)


# Review findings 2, 4, 5 (Sprint 0/1 review).

# Derrick Henry on the DK fixture: O/U 5.5 and his Ladder, as Fair Probabilities.
HENRY = [
    FairPoint(6, 0.513, 3.0), FairPoint(10, 0.358, 1.0), FairPoint(15, 0.235, 1.0),
    FairPoint(20, 0.161, 1.0), FairPoint(25, 0.115, 1.0), FairPoint(30, 0.083, 1.0),
    FairPoint(40, 0.044, 1.0), FairPoint(50, 0.027, 1.0),
]


def test_a_low_volume_fit_honours_its_main_line():
    # Finding 2: pi0 tied to mu made Henry's fit give P(Y >= 6) = 0.361 against the book's 0.513.
    fit = fit_receiving_yards(HENRY, "WR")
    # The rest of the miss is the WR gamma's shape: Henry is an RB (own shape in Sprint 4).
    assert fit.sf(np.array([6]))[0] == pytest.approx(0.513, abs=0.04)
    assert fit.ppf(np.array([0.5]))[0] in (5, 6)  # the Main Line's median, 5.5, not 0


def test_pi0_is_fitted_from_where_the_formula_puts_it():
    # Points simulated from a hurdle whose P(0) is well below the formula's for its mean.
    mu = 14.0
    formula_pi0 = pi0_relation(mu, "WR")  # ≈ 0.5
    truth = receiving_yards(mu, 16.0, "WR", pi0=0.30)
    xs = [5, 10, 15, 20, 30, 40]
    pts = [FairPoint(x, float(truth.sf(np.array([x]))[0]), 3.0 if x == 10 else 1.0) for x in xs]
    fit = fit_receiving_yards(pts, "WR")
    assert abs(fit.pi0 - 0.30) < abs(formula_pi0 - 0.30) / 2
    np.testing.assert_allclose(fit.sf(np.array(xs)), [p.prob for p in pts], atol=0.02)


def test_main_line_only_fit_keeps_the_formula_pi0():
    # One point can't pin pi0, so it stays on the formula.
    fit = fit_receiving_yards([FairPoint(53, 0.47, 3.0)], "WR")
    assert fit.pi0 == pytest.approx(pi0_relation(fit.mean, "WR"), rel=1e-9)


def test_a_ladder_only_fit_starts_from_its_mid_ladder_rung():
    # Finding 4: with equal weights the start was the first, most extreme Rung. A "10+" clipped
    # to 1 − 1e-4 by fair.PROB_FLOOR made the 1-point root-find raise "out of reach".
    truth = receiving_yards(55.0, 36.0, "WR")
    xs = [10, 30, 50, 70, 90, 110]
    pts = [FairPoint(x, float(truth.sf(np.array([x]))[0]), 1.0) for x in xs]
    pts[0] = FairPoint(10, 1 - 1e-4, 1.0)
    fit = fit_receiving_yards(pts, "WR")
    # The clipped Rung still pulls the fit (it is taken at face value), but only so far.
    assert fit.mean == pytest.approx(55.0, rel=0.2)


@pytest.mark.parametrize("xs", [[10, 20, 30], [2, 5, 10, 20], [30, 50, 70]])
@pytest.mark.parametrize("ps", [[0.03, 0.02, 0.01], [0.01, 0.005, 0.001], [1e-3, 5e-4, 1e-4]])
@pytest.mark.parametrize("main_weight", [1.0, 3.0])
def test_low_probabilities_everywhere_do_not_crash_the_optimizer(xs, ps, main_weight):
    # Finding 5: Rungs at 1-3% can push Nelder-Mead to tiny mu, where pi0 rounds to 1 and the
    # objective raised. A fit inside the model's range, or a FitError, is fine; nothing else is.
    from ffp.distributions import FitError

    pts = [FairPoint(x, p, main_weight if i == 0 else 1.0) for i, (x, p) in enumerate(zip(xs, ps))]
    try:
        fit = fit_receiving_yards(pts, "WR")
    except FitError:
        return
    assert 1.0 <= fit.mean <= 500.0 and 0 <= fit.pi0 < 1


# Poisson: shared by TDs (S3.2) and the Base Rate stats (S3.4).


def test_poisson_matches_scipy():
    from scipy import stats

    from ffp.distributions import Poisson

    d = Poisson(0.4)
    x = np.arange(-1, 6)
    np.testing.assert_allclose(d.sf(x), stats.poisson(0.4).sf(x - 1))  # P(Y >= x)
    assert d.mean == pytest.approx(0.4)
    u = np.array([0.1, 0.67, 0.671, 0.94, 0.9999])
    np.testing.assert_array_equal(d.ppf(u), stats.poisson(0.4).ppf(u))


def test_poisson_of_zero_is_always_zero():
    from ffp.distributions import Poisson

    d = Poisson(0.0)
    assert d.sf(np.array([1]))[0] == 0.0
    np.testing.assert_array_equal(d.ppf(np.array([0.01, 0.99])), [0, 0])


def test_poisson_rejects_a_negative_rate():
    from ffp.distributions import Poisson

    with pytest.raises(ValueError):
        Poisson(-0.1)


def test_receiving_yards_relations_come_from_the_params_file():
    # Sprint 3: the coefficients are generated by scripts/build_params.py, not written into the code.
    from ffp.distributions import receiving_yards_pi0_relation, receiving_yards_sd_relation

    for position in ("WR", "TE", "RB"):
        assert receiving_yards_pi0_relation(25.0, position) == pytest.approx(pi0_relation(25.0, position))
        assert receiving_yards_sd_relation(25.0, position) == pytest.approx(sd_relation(25.0, position))
    shift = params.load("dispersion")["receiving_yards"]["RB"]["shift"]
    assert receiving_yards(20.0, 15.0, "RB").shift == shift


# Sprint 3 review findings 7-9.


def test_prob_zero_and_prob_negative_for_every_family():
    # Finding 8: one definition of P(Y = 0) for the fit (yards pi0) and the simulation (no-catch mass).
    from scipy import stats

    from ffp.distributions import NegativeBinomial, Poisson, prob_negative, prob_zero

    assert prob_zero(Poisson(0.4)) == pytest.approx(math.exp(-0.4))
    nb = NegativeBinomial(3.0, 8.0)
    assert prob_zero(nb) == pytest.approx(stats.nbinom(8.0, 8.0 / 11.0).pmf(0))
    assert prob_negative(nb) == 0.0
    hurdle = receiving_yards(20.0, 18.0, "WR", pi0=0.25)
    assert prob_zero(hurdle) == pytest.approx(0.25)
    assert 0 < prob_negative(hurdle) < 0.05  # the shifted gamma's small negative-yardage mass


def test_coefficient_lookups_are_built_once():
    # Finding 7: the fit's objective looked the coefficients up thousands of times per player.
    from ffp.distributions import _receiving_params, _receptions_params

    assert _receiving_params("WR") is _receiving_params("WR")
    assert _receptions_params("WR") is _receptions_params("WR")
