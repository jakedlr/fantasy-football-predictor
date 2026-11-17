"""distributions.fit_yards: the yards fits with the Backtest's diagnostic switches (S5.2): no sd
prior (a free-fit sd, to check the dispersion relations) and a lognormal nonzero part (gamma vs
lognormal). Production fits are unchanged: the defaults are the production fit."""

import numpy as np
import pytest

from ffp.distributions import (
    HurdleShiftedLognormal,
    fit_passing_yards,
    fit_receiving_yards,
    fit_rushing_yards,
    fit_yards,
    passing_yards,
    passing_yards_sd_relation,
    receiving_yards,
    rushing_yards,
    rushing_yards_sd_relation,
    yards,
)
from ffp.fair import FairPoint


def points_from(dist, thresholds):
    return [FairPoint(t, float(dist.sf(np.array([t]))[0]), 1.0) for t in thresholds]


RUNGS = [10, 25, 40, 50, 60, 75, 90, 110]


def test_defaults_are_the_production_fits():
    truth = rushing_yards(55.0, 1.3 * rushing_yards_sd_relation(55.0, "RB"), "RB")
    pts = points_from(truth, RUNGS)
    assert fit_yards("rushing_yards", pts, "RB").sd == pytest.approx(fit_rushing_yards(pts, "RB").sd, rel=1e-12)
    wr = receiving_yards(60.0, 30.0, "WR", pi0=0.05)
    pts = points_from(wr, RUNGS)
    assert fit_yards("receiving_yards", pts, "WR", pi0=0.05).sd == pytest.approx(
        fit_receiving_yards(pts, "WR", pi0=0.05).sd, rel=1e-12)
    qb = passing_yards(240.0, 70.0)
    pts = points_from(qb, [150, 200, 225, 250, 275, 300])
    assert fit_yards("passing_yards", pts, "QB").sd == pytest.approx(fit_passing_yards(pts, "QB").sd, rel=1e-12)


@pytest.mark.parametrize("stat, make, position, thresholds", [
    ("rushing_yards", rushing_yards, "RB", RUNGS),
    ("passing_yards", passing_yards, "QB", [150, 200, 225, 250, 275, 300]),
])
def test_without_the_sd_prior_a_wide_player_is_recovered_exactly(stat, make, position, thresholds):
    mu = 55.0 if position == "RB" else 240.0
    rel = rushing_yards_sd_relation(mu, position) if position == "RB" else passing_yards_sd_relation(mu)
    truth = make(mu, 1.4 * rel, position)
    pts = points_from(truth, thresholds)
    family = "gamma" if stat == "rushing_yards" else None  # an RB's production fit is a sum of carries
    free = fit_yards(stat, pts, position, sd_prior=0.0, family=family)
    shrunk = fit_yards(stat, pts, position, family=family)
    assert free.sd == pytest.approx(truth.sd, rel=2e-3)
    assert shrunk.sd < free.sd


def test_a_lognormal_nonzero_part_keeps_the_mean_and_sd():
    dist = yards("rushing_yards", 60.0, 35.0, "RB", family="lognormal")
    assert isinstance(dist, HurdleShiftedLognormal)
    assert dist.mean == pytest.approx(60.0, rel=1e-9)
    gamma = yards("rushing_yards", 60.0, 35.0, "RB")
    # Same mean and sd: less mass around the P90 than the gamma (research Q1a's "thinner top
    # tail" at P95), more in the far tail.
    assert dist.sf(np.array([100]))[0] < gamma.sf(np.array([100]))[0]
    assert dist.sf(np.array([250]))[0] > gamma.sf(np.array([250]))[0]


def test_a_lognormal_fit_recovers_a_lognormal_player():
    truth = yards("receiving_yards", 60.0, 32.0, "WR", pi0=0.04, family="lognormal")
    fit = fit_yards("receiving_yards", points_from(truth, RUNGS), "WR", pi0=0.04, family="lognormal", sd_prior=0.0)
    assert isinstance(fit, HurdleShiftedLognormal)
    assert fit.mean == pytest.approx(60.0, rel=1e-3)
    assert fit.sd == pytest.approx(32.0, rel=1e-2)


def test_passing_yards_have_no_lognormal():
    with pytest.raises(ValueError, match="family"):
        fit_yards("passing_yards", [FairPoint(250, 0.5, 1.0)], "QB", family="lognormal")
