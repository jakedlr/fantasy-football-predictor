import pytest

from ffp import base_rates, params
from ffp.distributions import Poisson
from ffp.scoring import SCORING_STATS


@pytest.fixture
def br():
    return params.load("base_rates")


def fake_params(**overrides):
    doc = {
        "fumbles_lost_per_touch": {"RB": 0.01, "WR": 0.02, "TE": 0.03},
        "qb_fumbles_lost_per_sack": 0.05,
        "fumbles_lost_per_game": {"QB": 0.3, "RB": 0.1, "WR": 0.05, "TE": 0.04},
        "rush_rec_2pt_per_game": {"QB": 0.02, "RB": 0.04, "WR": 0.06, "TE": 0.08},
        "pass_2pt_per_game": {"QB": 0.1},
        "td_rushing_share": {"QB": 0.9, "RB": 0.75, "WR": 0.25, "TE": 0.1},
        "carries_per_game": {"WR": 0.5, "TE": 0.2},
    }
    doc.update(overrides)
    return doc


# --- expected touches ---------------------------------------------------------


def test_expected_touches_adds_receptions_and_carries():
    assert base_rates.expected_touches("WR", receptions_mean=5.5, carries_mean=1.5) == 7.0


def test_wr_without_carries_market_uses_carries_base_rate(br):
    carries = br["carries_per_game"]["WR"] or 0.0
    assert base_rates.expected_touches("WR", receptions_mean=6) == pytest.approx(6 + carries)


def test_injected_carries_base_rate_is_used():
    p = fake_params()
    assert base_rates.expected_touches("WR", receptions_mean=6, params=p) == pytest.approx(6.5)
    assert base_rates.expected_touches("TE", receptions_mean=4, params=p) == pytest.approx(4.2)


def test_null_carries_base_rate_counts_as_zero():
    p = fake_params(carries_per_game={"WR": None, "TE": None})
    assert base_rates.expected_touches("WR", receptions_mean=6, params=p) == 6.0


def test_missing_receptions_counts_as_zero():
    assert base_rates.expected_touches("WR", receptions_mean=None, carries_mean=2.0) == 2.0


def test_rb_without_carries_market_has_no_base_rate_to_fall_back_on():
    with pytest.raises(ValueError, match="carries"):
        base_rates.expected_touches("RB", receptions_mean=3.0, params=fake_params())


def test_rb_with_carries_market():
    assert base_rates.expected_touches("RB", receptions_mean=3.0, carries_mean=15.0) == 18.0


# --- fumbles lost -------------------------------------------------------------


@pytest.mark.parametrize("position", ["RB", "WR", "TE"])
def test_mean_fumbles_lost_is_rate_times_expected_touches(position, br):
    dist = base_rates.fumbles_lost(position, 7.5)
    assert isinstance(dist, Poisson)
    assert dist.mean == pytest.approx(br["fumbles_lost_per_touch"][position] * 7.5)


def test_fumbles_lost_uses_injected_rate():
    assert base_rates.fumbles_lost("TE", 4.0, params=fake_params()).mean == pytest.approx(0.12)


def test_qb_fumbles_lost_falls_back_to_per_game_rate(br):
    # No sacks market until S4.4: the flat per-game rate, whatever the touches.
    assert base_rates.fumbles_lost("QB", 5.0).mean == pytest.approx(br["fumbles_lost_per_game"]["QB"])
    assert base_rates.fumbles_lost("QB", 0.0, params=fake_params()).mean == pytest.approx(0.3)


def test_negative_expected_touches_is_rejected():
    with pytest.raises(ValueError):
        base_rates.fumbles_lost("WR", -1.0)


# --- two-point conversions ----------------------------------------------------


@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE"])
def test_two_point_split_by_td_share_sums_to_per_game_rate(position, br):
    two = base_rates.two_point_conversions(position)
    rate = br["rush_rec_2pt_per_game"][position]
    share = br["td_rushing_share"][position]
    assert two["rushing_2pt"].mean == pytest.approx(rate * share)
    assert two["receiving_2pt"].mean == pytest.approx(rate * (1 - share))
    assert two["rushing_2pt"].mean + two["receiving_2pt"].mean == pytest.approx(rate)


def test_only_qb_gets_passing_two_point(br):
    assert "passing_2pt" not in base_rates.two_point_conversions("WR")
    qb = base_rates.two_point_conversions("QB")
    assert qb["passing_2pt"].mean == pytest.approx(br["pass_2pt_per_game"]["QB"])


def test_two_point_uses_injected_params():
    two = base_rates.two_point_conversions("WR", params=fake_params())
    assert two["rushing_2pt"].mean == pytest.approx(0.015)
    assert two["receiving_2pt"].mean == pytest.approx(0.045)


# --- all together -------------------------------------------------------------


def test_base_rate_stats_for_wr():
    p = fake_params()
    stats = base_rates.base_rate_stats("WR", receptions_mean=6, params=p)
    assert set(stats) == {"fumbles_lost", "rushing_2pt", "receiving_2pt"}
    assert set(stats) <= set(SCORING_STATS)
    assert all(isinstance(d, Poisson) for d in stats.values())
    assert stats["fumbles_lost"].mean == pytest.approx(0.02 * 6.5)
    assert stats["receiving_2pt"].mean == pytest.approx(0.045)


@pytest.mark.parametrize("position", ["WR", "TE"])
def test_with_no_usage_market_fumbles_lost_is_the_per_game_rate(position):
    # Review finding 7: a missing receptions market counted as 0 catches, cutting a WR's fumbles to carries only.
    p = fake_params()
    stats = base_rates.base_rate_stats(position, receptions_mean=None, params=p)
    assert stats["fumbles_lost"].mean == pytest.approx(p["fumbles_lost_per_game"][position])


def test_td_rushing_share_is_read_from_the_base_rates():
    assert base_rates.td_rushing_share("TE", params=fake_params()) == 0.1
    with pytest.raises(ValueError):
        base_rates.td_rushing_share("K")


def test_base_rate_stats_for_qb_needs_no_touches():
    stats = base_rates.base_rate_stats("QB", receptions_mean=None, params=fake_params())
    assert set(stats) == {"fumbles_lost", "rushing_2pt", "receiving_2pt", "passing_2pt"}
    assert stats["fumbles_lost"].mean == pytest.approx(0.3)


@pytest.mark.parametrize(
    "call",
    [
        lambda: base_rates.expected_touches("K", receptions_mean=1.0),
        lambda: base_rates.fumbles_lost("K", 1.0),
        lambda: base_rates.two_point_conversions("DST"),
        lambda: base_rates.base_rate_stats("FB", receptions_mean=1.0),
    ],
)
def test_unknown_position_raises(call):
    with pytest.raises(ValueError, match="position"):
        call()


def test_committed_values_match_the_research_doc(br):
    # research/nflverse-data.md section 4, 2021-25 REG.
    assert br["fumbles_lost_per_touch"] == pytest.approx({"RB": 0.0044, "WR": 0.0065, "TE": 0.0058}, abs=0.002)
    assert br["fumbles_lost_per_game"]["QB"] == pytest.approx(0.204, abs=0.03)
    assert br["rush_rec_2pt_per_game"]["WR"] == pytest.approx(0.016, abs=0.01)
    assert br["pass_2pt_per_game"]["QB"] == pytest.approx(0.074, abs=0.02)
    assert br["td_rushing_share"]["WR"] == pytest.approx(0.033, abs=0.02)


# --- per-player TD share override (S4.3) ---------------------------------------


@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE"])
def test_two_point_split_by_a_players_own_share(position):
    p = fake_params()
    rate = p["rush_rec_2pt_per_game"][position]
    two = base_rates.two_point_conversions(position, p, rushing_share=0.4)
    assert two["rushing_2pt"].mean == pytest.approx(rate * 0.4)
    assert two["receiving_2pt"].mean == pytest.approx(rate * 0.6)


def test_base_rate_stats_passes_the_share_through():
    p = fake_params()
    stats = base_rates.base_rate_stats("RB", receptions_mean=3.0, carries_mean=15.0, params=p, rushing_share=0.9)
    assert stats["rushing_2pt"].mean == pytest.approx(0.04 * 0.9)
    assert stats["receiving_2pt"].mean == pytest.approx(0.04 * 0.1)


def test_no_share_override_keeps_the_position_share():
    p = fake_params()
    stats = base_rates.base_rate_stats("RB", receptions_mean=3.0, carries_mean=15.0, params=p, rushing_share=None)
    assert stats["rushing_2pt"].mean == pytest.approx(0.04 * 0.75)


@pytest.mark.parametrize("share", [-0.1, 1.1, float("nan")])
def test_share_override_out_of_range_is_rejected(share):
    with pytest.raises(ValueError, match="share"):
        base_rates.two_point_conversions("RB", fake_params(), rushing_share=share)
