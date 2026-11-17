"""predict.fit_player / stat_distributions / score_player: the fit-and-simulate step on its own,
from one player's Prop Markets, a position and a TD share (shared by `predict` and the Backtest)."""

import numpy as np
import pytest

from ffp import params
from ffp.distributions import prob_zero
from ffp.fair import KALSHI_PRIOR_C
from ffp.predict import fit_player, score_player, stat_distributions
from ffp.scoring import PRESETS
from ffp.simulate import simulate_scores
from ffp.sources.kalshi import KalshiRung, to_prop_markets

KALSHI = {"kalshi": KALSHI_PRIOR_C}
SLEEPER = PRESETS["sleeper"]


def rungs(stat, ladder, gsis_id="00-0036900", player="Ja'Marr Chase"):
    """KalshiRungs with the given {threshold: mid}, 1¢ either side."""
    return [KalshiRung("2026_10_PIT_CIN", 2026, 10, "2026-11-16T01:20:00Z", gsis_id, player, stat, k,
                       round(mid - 0.01, 4), round(mid + 0.01, 4)) for k, mid in ladder.items()]


def by_stat(*groups):
    out = {}
    for market in to_prop_markets([r for g in groups for r in g]):
        out.setdefault(market.stat, []).append(market)
    return out


WR = by_stat(
    rungs("receptions", {3: 0.85, 5: 0.6, 7: 0.3, 9: 0.1}),
    rungs("receiving_yards", {40: 0.8, 60: 0.6, 80: 0.4, 100: 0.25, 125: 0.12}),
    rungs("rush_rec_tds", {1: 0.5, 2: 0.15}),
)


def test_fit_player_fits_kalshi_markets_from_their_mids():
    seen = {}
    fitted = fit_player("WR", WR, 0.03, prior_c=KALSHI, on_fair_points=lambda stat, pts: seen.update({stat: pts}))
    assert [p.prob for p in seen["receiving_yards"]] == pytest.approx([0.8, 0.6, 0.4, 0.25, 0.12])
    assert fitted.flags == []
    assert set(fitted.dists) == {"receptions", "receiving_yards", "receiving_tds", "rushing_tds"}
    # Receptions set receiving yards' P(0).
    assert fitted.dists["receiving_yards"].pi0 == pytest.approx(prob_zero(fitted.dists["receptions"]))
    # The anytime price is kept whole as the TD total; the split shares its rate.
    assert fitted.tds.lam == pytest.approx(-np.log(0.5))
    assert fitted.dists["rushing_tds"].lam == pytest.approx(0.03 * fitted.tds.lam)


def test_fit_player_flags_a_missing_core_stat():
    fitted = fit_player("WR", {"rush_rec_tds": WR["rush_rec_tds"]}, 0.03, prior_c=KALSHI)
    assert "incomplete: missing receptions" in fitted.flags
    assert fitted.tds is not None


def test_stat_distributions_add_the_base_rates():
    dists = stat_distributions("WR", fit_player("WR", WR, 0.03, prior_c=KALSHI))
    assert {"fumbles_lost", "receiving_2pt", "rushing_2pt"} <= set(dists)


def test_score_player_none_is_the_independent_simulation():
    fitted = fit_player("WR", WR, 0.03, prior_c=KALSHI)
    scores = score_player("WR", fitted, SLEEPER, correlation="none", n=2000, seed=7)
    expected = simulate_scores(stat_distributions("WR", fitted), SLEEPER, n=2000, seed=7, corr=None)
    np.testing.assert_array_equal(scores.points, expected.points)


def test_score_player_default_uses_the_position_matrix():
    fitted = fit_player("WR", WR, 0.03, prior_c=KALSHI)
    scores = score_player("WR", fitted, SLEEPER, n=2000, seed=7)
    expected = simulate_scores(stat_distributions("WR", fitted), SLEEPER, n=2000, seed=7,
                               corr=params.correlation("WR"))
    np.testing.assert_array_equal(scores.points, expected.points)
