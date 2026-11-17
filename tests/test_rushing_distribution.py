"""Rushing yards and carries Stat Distributions (S4.2)."""

import inspect
import math

import numpy as np
import pytest
from scipy.special import expit

from ffp.distributions import main_line_only_kappa as kappa
from ffp import params
from ffp.distributions import (
    FitError,
    NegativeBinomial,
    fit_carries,
    fit_receiving_yards,
    fit_rushing_yards,
    fit_yards,
    receiving_yards,
    rushing_yards,
    rushing_yards_pi0_relation,
    rushing_yards_sd_relation,
)
from ffp.fair import FairPoint

POSITIONS = ("RB", "QB", "WR", "TE")


def relation(position):
    """The rushing-yards relations from the committed params: dispersion.json (scripts/build_params.py),
    with the P(0) relation refitted on priced players (market_calibration.json, S5.6) where there is one."""
    p = params.load("dispersion")["rushing_yards"][position]
    pi0 = params.load("market_calibration")["pi0"].get("rushing_yards", {}).get(position, {})
    return pi0.get("c0", p["pi0_c0"]), pi0.get("c1", p["pi0_c1"]), p["sd_a"], p["sd_b"], p["shift"]


def pi0_relation(mu, position):
    c0, c1, *_ = relation(position)
    return expit(c0 + c1 * math.log(mu))


def sd_relation(mu, position):
    _, _, a, b, _ = relation(position)
    return a * (mu / (1 - pi0_relation(mu, position))) ** b


def carries_r(mu, position):
    rel = params.load("dispersion")["carries"][position]
    return rel["r_a"] * mu ** rel["r_b"]


def points_from(dist, rungs, main_threshold):
    pts = [FairPoint(t, float(dist.sf(np.array([t]))[0]), 1.0) for t in rungs]
    if main_threshold is not None:
        pts.append(FairPoint(main_threshold, float(dist.sf(np.array([main_threshold]))[0]), 3.0))
    return sorted(pts, key=lambda p: p.threshold)


# --- rushing yards: the family --------------------------------------------------


@pytest.mark.parametrize("position, mu, sd", [("RB", 80.0, 40.0), ("QB", 20.0, 18.0), ("WR", 6.0, 8.0),
                                              ("TE", 3.0, 6.0)])
def test_rushing_yards_mean_is_the_requested_overall_mean(position, mu, sd):
    assert rushing_yards(mu, sd, position).mean == pytest.approx(mu, rel=1e-9)


@pytest.mark.parametrize("position", POSITIONS)
def test_rushing_yards_use_the_rushing_relations_and_shift(position):
    mu = 25.0
    dist = rushing_yards(mu, 20.0, position)
    assert dist.pi0 == pytest.approx(pi0_relation(mu, position), rel=1e-12)
    assert dist.shift == relation(position)[4]
    assert rushing_yards_pi0_relation(mu, position) == pytest.approx(pi0_relation(mu, position), rel=1e-12)
    assert rushing_yards_sd_relation(mu, position) == pytest.approx(sd_relation(mu, position), rel=1e-12)


def test_rushing_yards_differ_from_receiving_yards():
    # Same family, its own params: a WR's 20 rushing yards are not his 20 receiving yards.
    assert rushing_yards(20.0, 15.0, "WR").pi0 != pytest.approx(receiving_yards(20.0, 15.0, "WR").pi0)


def test_rushing_yards_take_a_given_pi0():
    assert rushing_yards(30.0, 20.0, "RB", pi0=0.1).pi0 == 0.1


def test_rushing_yards_go_negative():
    # A QB kneel or an RB stuffed: the shifted gamma's bins below 0 keep some mass.
    dist = rushing_yards(4.0, 8.0, "QB")
    assert 1 - dist.sf(np.array([0]))[0] > 0.01


def test_rushing_yards_reject_an_unknown_position():
    with pytest.raises(ValueError, match="K"):
        rushing_yards(20.0, 10.0, "K")


# --- rushing yards: fitting -----------------------------------------------------


@pytest.mark.parametrize("position, mu, rungs, main", [
    ("RB", 80.0, [40, 50, 60, 70, 90, 100, 120, 150], 82),
    ("QB", 25.0, [10, 15, 20, 30, 40, 50], 24),
    ("RB", 35.0, [10, 15, 20, 25, 30, 40, 50, 60], 35),
])
def test_fit_recovers_a_distribution_on_the_relation(position, mu, rungs, main):
    # The hurdle gamma's fit: production for a QB, the Backtest's comparison for an RB (S5.10).
    sd = sd_relation(mu, position)
    truth = rushing_yards(mu, sd, position)
    fit = fit_yards("rushing_yards", points_from(truth, rungs, main), position, family="gamma")
    assert fit.mean == pytest.approx(mu, rel=1e-3)
    assert fit.sd == pytest.approx(sd, rel=1e-3)
    assert fit.pi0 == pytest.approx(truth.pi0, abs=1e-3)


def test_main_line_only_reproduces_the_main_line_exactly():
    fit = fit_yards("rushing_yards", [FairPoint(82, 0.48, 3.0)], "RB", family="gamma")  # Henry, Over 81.5
    assert fit.sf(np.array([82]))[0] == pytest.approx(0.48, abs=1e-9)
    assert fit.sd == pytest.approx(kappa("rushing_yards", "RB") * sd_relation(fit.mean, "RB"), rel=1e-9)
    assert fit.pi0 == pytest.approx(pi0_relation(fit.mean, "RB"), rel=1e-9)


def test_fit_rushing_yards_takes_no_pi0_from_a_usage_market():
    # Sprint 4: carries don't set rushing-yards P(0); it's fitted with the δ prior.
    assert list(inspect.signature(fit_rushing_yards).parameters) == ["points", "position"]


def test_fit_rushing_yards_failures_are_fit_errors_but_an_unknown_position_is_not():
    with pytest.raises(FitError):
        fit_rushing_yards([], "RB")
    with pytest.raises(ValueError) as raised:
        fit_rushing_yards([FairPoint(30, 0.5, 3.0)], "K")
    assert not isinstance(raised.value, FitError)


def test_receiving_yards_still_reject_a_qb():
    with pytest.raises(ValueError):
        fit_receiving_yards([FairPoint(30, 0.5, 3.0)], "QB")


# --- carries (a Usage Stat) -------------------------------------------------------


@pytest.mark.parametrize("position, mu", [("RB", 19.0), ("RB", 10.5), ("QB", 5.5)])
def test_fit_carries_recovers_a_known_negative_binomial(position, mu):
    truth = NegativeBinomial(mu, carries_r(mu, position))
    rungs = [t for t in range(max(2, round(mu) - 8), round(mu) + 10, 2)]
    fit = fit_carries(points_from(truth, rungs, main_threshold=round(mu)), position)
    assert fit.mean == pytest.approx(mu, rel=1e-3)
    assert fit.r == pytest.approx(truth.r, rel=1e-3)


@pytest.mark.parametrize("position", POSITIONS)
def test_carries_main_line_only_uses_the_carries_relation(position):
    fit = fit_carries([FairPoint(4, 0.45, 3.0)], position)  # Justice Hill: Over 3.5, no Ladder
    assert fit.sf(np.array([4]))[0] == pytest.approx(0.45, abs=1e-9)
    assert fit.r == pytest.approx(carries_r(fit.mean, position), rel=1e-9)


def test_fit_carries_failures_are_fit_errors_but_an_unknown_position_is_not():
    with pytest.raises(FitError):
        fit_carries([], "RB")
    with pytest.raises(ValueError) as raised:
        fit_carries([FairPoint(10, 0.5, 3.0)], "K")
    assert not isinstance(raised.value, FitError)
