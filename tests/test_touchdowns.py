"""Rush + rec TDs (S3.2): anytime TD → Poisson λ, checked against 2+, split rushing vs receiving."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from ffp import params
from ffp.distributions import FitError, Poisson, fit_touchdowns, split_touchdowns
from ffp.fair import FairPoint, fair_points
from ffp.sources import draftkings, fanduel

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def fixture_td_markets() -> dict[str, list]:
    """Each fixture player's TD Prop Markets from both books, by name as the book spells it."""
    a, b = load("dk_nfl_td_scorer.json"), load("dk_nfl_3plus_tds.json")
    dk_json = {"events": a["events"], "markets": a["markets"] + b["markets"],
               "selections": a["selections"] + b["selections"]}
    by_player: dict[str, list] = {}
    for m in draftkings.parse_prop_markets(dk_json, None, "rush_rec_tds") + fanduel.parse_prop_markets(
        load("fd_nfl_event_td_scorer_tab.json"), "rush_rec_tds"
    ):
        by_player.setdefault(m.player, []).append(m)
    return by_player


# --- fit ------------------------------------------------------------------------------


def test_lambda_comes_from_the_anytime_point():
    td = fit_touchdowns([FairPoint(1, 0.40, 1.0), FairPoint(2, 0.09, 1.0)])
    assert isinstance(td, Poisson)
    assert td.lam == pytest.approx(-math.log(1 - 0.40), rel=1e-12)
    assert td.sf(np.array([1]))[0] == pytest.approx(0.40, rel=1e-12)


def test_the_2plus_and_3plus_points_do_not_move_lambda():
    # They are a check on the vig model, not a fit input (see fit_touchdowns).
    alone = fit_touchdowns([FairPoint(1, 0.40, 1.0)])
    with_more = fit_touchdowns([FairPoint(1, 0.40, 1.0), FairPoint(2, 0.20, 1.0), FairPoint(3, 0.1, 1.0)])
    assert with_more.lam == alone.lam


def test_without_an_anytime_point_the_lowest_threshold_is_matched():
    td = fit_touchdowns([FairPoint(2, 0.06, 1.0), FairPoint(3, 0.01, 1.0)])
    assert td.sf(np.array([2]))[0] == pytest.approx(0.06, abs=1e-10)


def test_no_points_is_a_fit_error():
    with pytest.raises(FitError):
        fit_touchdowns([])


def test_two_plus_price_is_consistent_with_the_fitted_poisson():
    # PLAN S3.2, on every fixture player with both an anytime and a 2+ price (both books merged,
    # devigged with the books' prior vig since these are one-sided). Observed gaps, Poisson P(2+)
    # minus the 2+ Fair Probability: −0.5 to +1.0 pp for everyone but Derrick Henry (RB, anytime
    # 56%), who is +2.3 pp; for the longshots it's ≈ −0.3 pp, the 2+ price's heavier vig.
    gaps = {}
    for player, markets in fixture_td_markets().items():
        fair = {p.threshold: p.prob for p in fair_points(markets)}
        if 1 not in fair or 2 not in fair:
            continue
        gaps[player] = float(fit_touchdowns(fair_points(markets)).sf(np.array([2]))[0]) - fair[2]
    assert len(gaps) >= 25
    assert max(abs(g) for g in gaps.values()) < 0.025
    wr_te = ["Zay Flowers", "Tre Harris", "Mark Andrews", "Rashod Bateman", "Quentin Johnston", "Ja'Kobi Lane"]
    assert all(abs(gaps[p]) < 0.012 for p in wr_te)


def test_three_plus_is_consistent_too():
    gaps = []
    for markets in fixture_td_markets().values():
        fair = {p.threshold: p.prob for p in fair_points(markets)}
        if 3 in fair:
            gaps.append(float(fit_touchdowns(fair_points(markets)).sf(np.array([3]))[0]) - fair[3])
    assert len(gaps) >= 15
    assert max(abs(g) for g in gaps) < 0.01


# --- split ----------------------------------------------------------------------------


def test_split_is_poisson_thinning():
    td = Poisson(0.5)
    rushing, receiving = split_touchdowns(td, 0.2)
    assert isinstance(rushing, Poisson) and isinstance(receiving, Poisson)
    assert rushing.lam == pytest.approx(0.1) and receiving.lam == pytest.approx(0.4)
    assert rushing.lam + receiving.lam == pytest.approx(td.lam)


def test_split_with_the_base_rate_share_for_a_wr():
    share = params.load("base_rates")["td_rushing_share"]["WR"]
    rushing, receiving = split_touchdowns(Poisson(0.44), share)
    assert rushing.lam == pytest.approx(0.44 * share)
    assert receiving.lam == pytest.approx(0.44 * (1 - share))


@pytest.mark.parametrize("share", [0.0, 1.0])
def test_split_at_the_ends_gives_a_zero_rate(share):
    rushing, receiving = split_touchdowns(Poisson(0.3), share)
    assert {rushing.lam, receiving.lam} == {0.0, 0.3}


@pytest.mark.parametrize("share", [-0.1, 1.1, math.nan])
def test_split_rejects_an_impossible_share(share):
    with pytest.raises(ValueError):
        split_touchdowns(Poisson(0.3), share)
