"""Rushing yards as a sum of carries (S5.10, docs/adr/0002)."""

import math

import numpy as np
import pytest
from scipy import stats

from ffp.distributions import (
    CarriesCompound,
    FitError,
    HurdleShiftedGamma,
    PerCarry,
    carries_compound,
    carries_r_relation,
    compound_rushing_yards,
    fit_carries_compound,
    fit_rushing_yards,
    per_carry,
    prob_negative,
    prob_zero,
)
from ffp.fair import FairPoint

# A toy carry: -2 (10%), 0 (10%), 3 (50%), 10 (30%).
TOY = PerCarry(-2, (0.1, 0.0, 0.1, 0.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.3))


def brute_force(lam, r, carry: PerCarry, n_max=60):
    """P(Y = y) by summing NB(N = n) × the n-fold convolution of one carry."""
    nb = stats.nbinom(r, r / (r + lam))
    one = np.asarray(carry.pmf)
    out, conv = {}, np.array([1.0])
    for n in range(n_max):
        lo = n * carry.lo
        for i, p in enumerate(conv):
            out[lo + i] = out.get(lo + i, 0.0) + nb.pmf(n) * p
        conv = np.convolve(conv, one)
    return out


def test_the_pmf_is_the_nb_sum_of_carries():
    dist = CarriesCompound(2.5, 1.0, 1.7, TOY)
    want = brute_force(2.5, 1.7, TOY)
    for y in (-4, -2, 0, 3, 6, 10, 13, 23, 40):
        got = dist.sf(np.array([y]))[0] - dist.sf(np.array([y + 1]))[0]
        assert got == pytest.approx(want.get(y, 0.0), abs=1e-10)


def test_mean_is_expected_carries_times_yards_per_carry():
    dist = CarriesCompound(4.0, 1.0, 2.0, TOY)
    assert dist.mean == pytest.approx(4.0 * TOY.mean, rel=1e-9)


def test_p0_includes_no_carries_and_sums_that_land_on_zero():
    dist = CarriesCompound(1.0, 1.0, 1.0, TOY)
    no_carries = stats.nbinom(1.0, 0.5).pmf(0)
    assert prob_zero(dist) > no_carries
    assert dist.pi0 == pytest.approx(prob_zero(dist), rel=1e-12)


def test_scale_stretches_each_carry():
    k = np.arange(TOY.lo, TOY.lo + len(TOY.pmf))
    for s in (0.8, 1.25):
        x = TOY.scaled(s)
        assert x.pmf.sum() == pytest.approx(1.0)
        # Re-binning to whole yards moves the mean a little (here 0.4%).
        assert float(x.values @ x.pmf) == pytest.approx(s * float(k @ np.asarray(TOY.pmf)), rel=0.01)


def test_sf_and_ppf_agree():
    dist = carries_compound(8.0, "RB")
    u = np.array([0.01, 0.1, 0.5, 0.9, 0.99])
    y = dist.ppf(u)
    assert np.all(1 - dist.sf(y + 1) >= u - 1e-12)  # P(Y <= y) >= u
    assert np.all(1 - dist.sf(y) < u)  # and y is the smallest such
    assert dist.sf(np.array([-1000]))[0] == pytest.approx(1.0)
    assert dist.sf(np.array([5000]))[0] == pytest.approx(0.0, abs=1e-12)


def test_r_comes_from_the_carries_relation():
    dist = carries_compound(10.0, "RB", r_mult=2.0)
    assert dist.r == pytest.approx(2.0 * carries_r_relation(10.0, "RB"))


@pytest.mark.parametrize("mu", [5.0, 12.0, 18.0])
def test_a_low_volume_back_has_little_mass_below_zero(mu):
    # The point of S5.10: nflverse RBs at expected 1-20 yards finish below 0 about 2-3% of the
    # time; the shifted gamma gave 10-16%.
    assert prob_negative(compound_rushing_yards(mu, "RB")) < 0.05


def test_a_low_volume_back_keeps_a_long_right_tail():
    # nflverse: nonzero games at expected 10-15 yards reach 40+ about 12% of the time.
    assert compound_rushing_yards(12.0, "RB").sf(np.array([40]))[0] > 0.04


@pytest.mark.parametrize("position", ["RB", "WR", "TE"])
def test_an_estimated_mean_is_exact(position):
    assert compound_rushing_yards(37.0, position).mean == pytest.approx(37.0, rel=1e-9)


def test_a_mean_out_of_range_is_a_fit_error():
    with pytest.raises(FitError):
        compound_rushing_yards(1e4, "RB")
    with pytest.raises(ValueError):
        per_carry("QB")  # QBs keep the hurdle gamma


def test_main_line_only_reproduces_the_main_line_on_the_relations():
    fit = fit_carries_compound([FairPoint(45, 0.47, 3.0)], "RB")
    assert fit.sf(np.array([45]))[0] == pytest.approx(0.47, abs=1e-8)
    assert (fit.scale, fit.r) == (1.0, pytest.approx(carries_r_relation(fit.lam, "RB")))


def test_a_ladder_recovers_the_carries_scale_and_dispersion():
    truth = carries_compound(14.0, "RB", scale=1.1, r_mult=1.5)
    rungs = [10, 25, 40, 50, 60, 75, 90]
    pts = [FairPoint(k, float(truth.sf(np.array([k]))[0]), 3.0 if k == 60 else 1.0) for k in rungs]
    fit = fit_carries_compound(pts, "RB")
    assert fit.mean == pytest.approx(truth.mean, rel=0.01)
    for k in rungs:  # the priors pull s and r_mult a little toward 1; the Rungs still fit closely
        assert fit.sf(np.array([k]))[0] == pytest.approx(truth.sf(np.array([k]))[0], abs=0.01)


def test_out_of_reach_is_a_fit_error():
    with pytest.raises(FitError):
        fit_carries_compound([FairPoint(400, 0.9, 3.0)], "RB")
    with pytest.raises(FitError):
        fit_carries_compound([], "RB")


def test_rushing_yards_are_a_sum_of_carries_except_for_a_qb():
    pts = [FairPoint(30, 0.5, 3.0)]
    assert isinstance(fit_rushing_yards(pts, "RB"), CarriesCompound)
    assert isinstance(fit_rushing_yards(pts, "WR"), CarriesCompound)
    assert isinstance(fit_rushing_yards(pts, "QB"), HurdleShiftedGamma)


def test_per_carry_params_are_a_distribution():
    for position in ("RB", "WR", "TE"):
        pc = per_carry(position)
        assert sum(pc.pmf) == pytest.approx(1.0)
        assert pc.lo < 0 and 3.5 < pc.mean < 7.0
        assert not math.isnan(pc.mean)
