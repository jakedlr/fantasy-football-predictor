"""Estimated Stats (S5.4): a core stat with no Prop Market, estimated from other stats and recent games."""

import numpy as np
import pytest

from ffp import estimates
from ffp.estimates import RecentGames, estimate_mean, estimated_distribution, load_recent_games

PARAMS = {
    "stat_ratios": {
        "receptions": {"receiving_yards": {"WR": 0.08, "RB": 0.12, "QB": None}},
        "receiving_yards": {"receptions": {"WR": 12.5}},
        "rushing_yards": {"carries": {"RB": 4.0}, "rush_rec_tds": {"RB": 100.0}},
        "rush_rec_tds": {"rushing_yards": {"RB": 0.009, "WR": 0.01}, "receiving_yards": {"RB": 0.004, "WR": 0.006}},
        "passing_tds": {"passing_yards": {"QB": 0.006}},
    }
}


def test_from_another_priced_stat_alone():
    # Receptions from a 60-yard receiving mean: 60 × 0.08.
    assert estimate_mean("receptions", "WR", {"receiving_yards": 60.0}, None, PARAMS) == pytest.approx(4.8)


def test_the_first_priced_source_is_used():
    # Rushing yards come from carries when carries are priced, not from TDs.
    means = {"carries": 15.0, "rush_rec_tds": 0.5}
    assert estimate_mean("rushing_yards", "RB", means, None, PARAMS) == pytest.approx(60.0)
    assert estimate_mean("rushing_yards", "RB", {"rush_rec_tds": 0.5}, None, PARAMS) == pytest.approx(50.0)


def test_touchdowns_sum_a_rushing_and_a_receiving_part():
    means = {"rushing_yards": 60.0, "receiving_yards": 20.0}  # 0.54 rushing + 0.08 receiving
    assert estimate_mean("rush_rec_tds", "RB", means, None, PARAMS) == pytest.approx(0.62)
    assert estimate_mean("rush_rec_tds", "WR", {"receiving_yards": 50.0}, None, PARAMS) == pytest.approx(0.30)


def test_recent_games_are_shrunk_toward_the_other_stat_estimate():
    recent = RecentGames(games=8, totals={"receptions": 48.0})  # 6.0 a game
    got = estimate_mean("receptions", "WR", {"receiving_yards": 60.0}, recent, PARAMS)  # (a) = 4.8
    assert got == pytest.approx(0.4 * 6.0 + 0.6 * 4.8)  # 8 games against 12 pseudo-games


def test_recent_games_alone_when_no_other_stat_helps():
    recent = RecentGames(games=4, totals={"receptions": 12.0})
    assert estimate_mean("receptions", "WR", {"rush_rec_tds": 0.3}, recent, PARAMS) == pytest.approx(3.0)


def test_nothing_to_go_on_gives_no_estimate():
    assert estimate_mean("receptions", "WR", {"rush_rec_tds": 0.3}, None, PARAMS) is None
    assert estimate_mean("receptions", "WR", {}, RecentGames(games=0, totals={}), PARAMS) is None
    assert estimate_mean("receptions", "QB", {"receiving_yards": 5.0}, None, PARAMS) is None  # a null ratio


@pytest.mark.parametrize(
    "stat, position, mean",
    [("receptions", "WR", 4.8), ("receiving_yards", "TE", 35.0), ("rushing_yards", "RB", 55.0),
     ("passing_yards", "QB", 230.0), ("passing_tds", "QB", 1.5), ("passing_interceptions", "QB", 0.7),
     ("rush_rec_tds", "RB", 0.6)],
)
def test_the_estimated_distribution_has_the_estimated_mean(stat, position, mean):
    dist = estimated_distribution(stat, position, mean)
    assert dist.mean == pytest.approx(mean, rel=1e-6)
    assert 0 <= dist.sf(np.array([1]))[0] <= 1


def test_estimated_receiving_yards_take_p0_from_receptions():
    receptions = estimated_distribution("receptions", "WR", 3.0)
    yards = estimated_distribution("receiving_yards", "WR", 36.0, receptions=receptions)
    p_no_catch = 1 - receptions.sf(np.array([1]))[0]
    assert yards.pi0 == pytest.approx(p_no_catch)


def test_a_mean_the_model_cannot_carry_gives_no_distribution():
    assert estimated_distribution("receiving_yards", "WR", 0.0) is None
    assert estimated_distribution("passing_tds", "QB", 12.0) is None


# --- recent games from nflverse ------------------------------------------------------


def rows(season, weeks, **stats):
    return [{"player_id": "p1", "season": season, "week": w, **stats} for w in weeks]


def test_recent_games_are_the_last_n_before_the_game():
    fetch = {2025: rows(2025, range(1, 18), receptions=2, receiving_yards=20),
             2026: rows(2026, range(1, 6), receptions=6, receiving_yards=80)}.get
    recent = load_recent_games(2026, fetch=fetch, before=(2026, 4), n=8)["p1"]
    # Weeks 1-3 of 2026 and the last 5 of 2025.
    assert recent.games == 8
    assert recent.totals["receptions"] == 3 * 6 + 5 * 2
    assert recent.mean("receiving_yards") == pytest.approx((3 * 80 + 5 * 20) / 8)


def test_touchdowns_are_rushing_plus_receiving():
    fetch = {2026: rows(2026, [1, 2], rushing_tds=1, receiving_tds="NA")}.get
    recent = load_recent_games(2026, fetch=lambda s: fetch(s) or [])["p1"]
    assert recent.totals["rush_rec_tds"] == 2


def test_an_unstarted_season_and_a_failed_fetch():
    def not_yet(season):
        if season == 2027:
            raise ConnectionError("404 not found")
        return rows(season, [17, 18], receptions=4)

    assert load_recent_games(2027, fetch=not_yet)["p1"].games == 2

    def broken(season):
        raise ValueError("bad parquet")

    assert load_recent_games(2026, fetch=broken) == {}
