"""scripts/build_params.py: the computations on tiny synthetic frames (no network)."""

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from scipy import special, stats

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_params", ROOT / "scripts" / "build_params.py")
bp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bp)


def stat_rows(rows):
    """player_stats-shaped frame: every STAT_COLUMN defaults to 0."""
    full = [{**{c: 0.0 for c in bp.STAT_COLUMNS}, "season_type": "REG", "team": "AAA", **r} for r in rows]
    return pl.DataFrame(full)


def test_parse_seasons():
    assert bp.parse_seasons("2021-2025") == [2021, 2022, 2023, 2024, 2025]
    assert bp.parse_seasons("2023,2021") == [2021, 2023]
    assert bp.parse_seasons("2019") == [2019]
    assert bp.season_label([2021, 2022, 2023]) == "2021-2023"
    assert bp.season_label([2021, 2023]) == "2021,2023"


# ---------------------------------------------------------------- data


@pytest.fixture
def raw_frames():
    stats_ = stat_rows([
        {"player_id": "W1", "position": "WR", "season": 2023, "week": 1, "game_id": "g1", "receptions": 5.0},
        {"player_id": "L1", "position": "LB", "season": 2023, "week": 1, "game_id": "g1"},
    ])
    snaps = pl.DataFrame({
        "pfr_player_id": ["w1", "w1", "t1", "t2"],
        "game_id": ["g1", "g2", "g1", "g1"],
        "season": [2023] * 4, "week": [1, 2, 1, 1],
        "game_type": ["REG", "WC", "REG", "REG"], "team": ["AAA"] * 4,
        "offense_snaps": [40.0, 30.0, 20.0, 0.0], "offense_pct": [0.8, 0.6, 0.3, 0.0],
    })
    players = pl.DataFrame({"gsis_id": ["W1", "T1", "T2", "L1"], "pfr_id": ["w1", "t1", "t2", None],
                            "position": ["WR", "TE", "TE", "LB"]})
    return stats_, snaps, players


def test_skill_games_joins_snap_share_and_drops_non_skill(raw_frames):
    games = bp.skill_games(*raw_frames)
    assert games["player_id"].to_list() == ["W1"]
    assert games["offense_pct"].to_list() == [0.8]


def test_skill_games_recovers_zero_lines_from_snaps(raw_frames):
    games = bp.skill_games(*raw_frames, recover_zeros=True)
    rows = {(r["player_id"], r["game_id"]): r for r in games.to_dicts()}
    # W1 played g2 (a playoff game) without a stats row; T1 played g1; T2 took no snaps.
    assert set(rows) == {("W1", "g1"), ("W1", "g2"), ("T1", "g1")}
    assert rows[("W1", "g2")]["season_type"] == "POST"
    assert rows[("T1", "g1")]["position"] == "TE"
    assert rows[("T1", "g1")]["receptions"] == 0.0


def test_qualifying_games_uses_position_filters():
    games = stat_rows([
        {"player_id": "Q", "position": "QB", "season": 2023, "week": 1, "game_id": "g", "attempts": 15.0, "offense_pct": 0.1},
        {"player_id": "Q", "position": "QB", "season": 2023, "week": 2, "game_id": "h", "attempts": 14.0, "offense_pct": 1.0},
        {"player_id": "R", "position": "RB", "season": 2023, "week": 1, "game_id": "g", "offense_pct": 0.30},
        {"player_id": "W", "position": "WR", "season": 2023, "week": 1, "game_id": "g", "offense_pct": 0.49},
        {"player_id": "W", "position": "WR", "season": 2023, "week": 19, "game_id": "p", "offense_pct": 0.9, "season_type": "POST"},
    ])
    assert bp.qualifying_games(games, "QB")["game_id"].to_list() == ["g"]
    assert bp.qualifying_games(games, "RB").height == 1
    assert bp.qualifying_games(games, "WR").height == 0  # 49% snaps, and the playoff game is excluded


# ---------------------------------------------------------------- correlation


def test_spearman_matrix_matches_scipy():
    rng = np.random.default_rng(1)
    x = rng.normal(size=200)
    df = pl.DataFrame({"a": x, "b": x + rng.normal(size=200), "c": rng.integers(0, 2, 200).astype(float)})
    expected = stats.spearmanr(df.to_numpy()).statistic
    np.testing.assert_allclose(bp.spearman_matrix(df, ["a", "b", "c"]), expected, atol=1e-12)


def test_within_player_removes_between_player_differences():
    # Two players at very different levels; inside each player-season a and b are anti-ordered.
    rows = []
    for pid, level in (("A", 0.0), ("B", 100.0)):
        for i in range(6):
            rows.append({"player_id": pid, "season": 2023, "a": level + i, "b": level + 5 - i})
    df = pl.DataFrame(rows)
    assert bp.spearman_matrix(df, ["a", "b"])[0, 1] > 0.4  # pooled: driven by the level gap
    np.testing.assert_allclose(bp.within_spearman_matrix(df, ["a", "b"]), [[1, -1], [-1, 1]])


def test_within_player_sample_needs_min_games():
    df = pl.DataFrame({"player_id": ["A"] * 6 + ["B"] * 5 + ["A"] * 2,
                       "season": [2023] * 11 + [2024] * 2, "x": list(range(13))})
    kept = bp.within_player_sample(df)
    assert kept.height == 6 and set(kept["player_id"]) == {"A"} and set(kept["season"]) == {2023}


def test_latent_rho_recovers_rho_for_continuous_margins():
    z = np.random.default_rng(0).standard_normal((40_000, 2))
    x = np.random.default_rng(2).gamma(2.0, size=5_000)
    y = np.random.default_rng(3).normal(size=5_000)
    for rho in (-0.4, 0.3, 0.75):
        target = 6 / math.pi * math.asin(rho / 2)  # Spearman of a bivariate normal
        assert bp.latent_rho(x, y, target, z) == pytest.approx(rho, abs=0.02)


def test_latent_rho_exceeds_spearman_for_tied_td_margins():
    z = np.random.default_rng(0).standard_normal((40_000, 2))
    yards = np.random.default_rng(1).gamma(3.0, 20.0, size=5_000)
    tds = np.random.default_rng(2).poisson(0.3, size=5_000).astype(float)
    rho = bp.latent_rho(yards, tds, 0.30, z)
    assert rho > 0.38
    assert bp.simulated_spearman(yards, tds, rho, z) == pytest.approx(0.30, abs=0.005)


def test_latent_matrix_is_symmetric_with_unit_diagonal():
    rng = np.random.default_rng(4)
    x = rng.normal(size=3_000)
    df = pl.DataFrame({"a": x, "b": x + rng.normal(size=3_000), "c": rng.poisson(0.4, 3_000).astype(float)})
    target = bp.spearman_matrix(df, ["a", "b", "c"])
    r = bp.latent_matrix(df, ["a", "b", "c"], target, draws=20_000)
    np.testing.assert_allclose(r, r.T)
    np.testing.assert_allclose(np.diag(r), 1.0)
    assert r[0, 1] == pytest.approx(math.sin(math.pi * target[0, 1] / 6) * 2, abs=0.03)


def test_make_positive_definite_repairs_and_leaves_pd_alone():
    bad = np.array([[1.0, 0.9, -0.9], [0.9, 1.0, 0.9], [-0.9, 0.9, 1.0]])
    assert np.linalg.eigvalsh(bad).min() < 0
    fixed = bp.make_positive_definite(bad)
    assert np.linalg.eigvalsh(fixed).min() > 0
    np.testing.assert_allclose(np.diag(fixed), 1.0)
    good = np.array([[1.0, 0.5], [0.5, 1.0]])
    np.testing.assert_array_equal(bp.make_positive_definite(good), good)


def test_rounded_turns_negative_zero_into_zero():
    out = bp.rounded(np.array([[1.0, -0.0001], [-0.0001, 1.0]]))
    assert out == [[1.0, 0.0], [0.0, 1.0]]
    assert math.copysign(1, out[0][1]) == 1


def test_correlation_entry_keeps_the_params_schema():
    rng = np.random.default_rng(5)
    rows = [{"player_id": f"P{i // 8}", "season": 2023, "receptions": float(rng.poisson(4)),
             "receiving_yards": float(rng.gamma(2, 25)), "receiving_tds": float(rng.poisson(0.3)),
             "rushing_yards": 0.0} for i in range(80)]
    cols = bp.CORRELATION_STATS["WR"]
    entry = bp.correlation_entry(pl.DataFrame(rows), cols, "within", draws=5_000, seed=0)
    assert entry["stats"] == cols
    r = np.array(entry["matrix"])
    assert r.shape == (4, 4) and np.linalg.eigvalsh(r).min() > 0
    assert np.array(entry["spearman"])[3].tolist() == [0.0, 0.0, 0.0, 1.0]  # constant rushing_yards
    assert entry["n_games"] == 80 and entry["n_players"] == 10


# ---------------------------------------------------------------- base rates


def test_base_rates_hand_computed():
    def pos(p, rows):
        return stat_rows([{"player_id": p, "position": p, "season": 2023, "week": i, "game_id": f"g{i}", **r}
                          for i, r in enumerate(rows)])

    qual = {
        "QB": pos("QB", [{"sacks_suffered": 4.0, "sack_fumbles_lost": 1.0, "fumbles_lost_total": 1.0,
                          "passing_2pt_conversions": 1.0, "rushing_tds": 1.0, "attempts": 30.0},
                         {"carries": 2.0, "rushing_yards": 10.0, "rushing_fumbles_lost": 1.0, "fumbles_lost_total": 1.0,
                          "attempts": 20.0}]),
        "RB": pos("RB", [{"carries": 18.0, "rushing_yards": 72.0, "receptions": 2.0, "fumbles_lost_total": 1.0,
                          "rushing_tds": 3.0, "receiving_tds": 1.0, "rushing_2pt_conversions": 1.0}]),
        "WR": pos("WR", [{"carries": 1.0, "rushing_yards": 6.0, "receptions": 4.0, "fumbles_lost_total": 1.0,
                          "receiving_tds": 1.0},
                         {"receptions": 5.0, "receiving_2pt_conversions": 1.0}]),
        "TE": pos("TE", [{"receptions": 4.0, "receiving_tds": 2.0}]),
    }
    r = bp.base_rates(qual)
    assert r["fumbles_lost_per_touch"] == {"RB": 0.05, "WR": 0.1, "TE": 0.0}
    assert r["qb_fumbles_lost_per_sack"] == 0.25
    assert r["fumbles_lost_per_game"] == {"QB": 1.0, "RB": 1.0, "WR": 0.5, "TE": 0.0}
    assert r["rush_rec_2pt_per_game"] == {"QB": 0.0, "RB": 1.0, "WR": 0.5, "TE": 0.0}
    assert r["pass_2pt_per_game"] == {"QB": 0.5}
    assert r["td_rushing_share"] == {"QB": 1.0, "RB": 0.75, "WR": 0.0, "TE": 0.0}
    assert r["carries_per_game"] == {"WR": 0.5, "TE": 0.0}
    assert r["n_games"] == {"QB": 2, "RB": 1, "WR": 2, "TE": 1}
    # Sprint 4: QB fumbles = sack fumbles + rush-attempt fumbles (S4.4), and carries from a
    # rushing-yards mean where there is no rush-attempts market (FanDuel has none).
    assert r["qb_sacks_per_game"] == 2.0
    assert r["qb_sacks_per_attempt"] == 0.08
    assert r["qb_fumbles_lost_per_carry"] == 0.5
    assert r["yards_per_carry"] == {"QB": 5.0, "RB": 4.0, "WR": 6.0, "TE": None}  # TE: no carries


def test_td_share_constants_hand_computed():
    # S4.3: the volume share's per-position TD rates come from the same qualifying games as m.
    def pos(p, rows):
        return stat_rows([{"player_id": p, "position": p, "season": 2023, "week": i, "game_id": f"g{i}", **r}
                          for i, r in enumerate(rows)])

    qual = {
        "QB": pos("QB", [{"carries": 5.0, "rushing_tds": 1.0, "targets": 1.0, "attempts": 30.0, "sacks_suffered": 2.0},
                         {"carries": 15.0, "rushing_tds": 1.0}]),
        "RB": pos("RB", [{"carries": 20.0, "rushing_tds": 1.0, "targets": 4.0, "receiving_tds": 1.0},
                         {"carries": 30.0, "targets": 6.0}]),
        "WR": pos("WR", [{"carries": 2.0, "targets": 40.0, "receiving_tds": 2.0}]),
        "TE": pos("TE", [{"targets": 8.0, "receptions": 6.0, "receiving_tds": 1.0}]),
    }
    share = bp.base_rates(qual)["td_share"]
    assert share["rho_rush"] == {"QB": 0.1, "RB": 0.02, "WR": 0.0, "TE": None}  # TE: no carries
    assert share["rho_rec"] == {"QB": 0.0, "RB": 0.1, "WR": 0.05, "TE": 0.125}
    assert share["kappa"] == 2
    assert share["k"] == 30
    assert share["prior_seasons"] == 2


def test_stat_ratios_hand_computed():
    # S5.4: an Estimated Stat's mean from another stat's mean, e.g. receptions = receiving yards / 12.
    def pos(p, rows):
        return stat_rows([{"player_id": p, "position": p, "season": 2023, "week": i, "game_id": f"g{i}", **r}
                          for i, r in enumerate(rows)])

    qual = {
        "QB": pos("QB", [{"passing_yards": 250.0, "passing_tds": 2.0, "passing_interceptions": 1.0, "attempts": 30.0,
                          "sacks_suffered": 2.0, "carries": 4.0, "rushing_yards": 20.0, "rushing_tds": 1.0},
                         {"passing_yards": 150.0, "carries": 6.0, "rushing_yards": 20.0}]),
        "RB": pos("RB", [{"carries": 20.0, "rushing_yards": 80.0, "rushing_tds": 1.0, "receptions": 2.0,
                          "receiving_yards": 20.0, "targets": 3.0}]),
        "WR": pos("WR", [{"receptions": 5.0, "receiving_yards": 60.0, "receiving_tds": 1.0, "targets": 8.0}]),
        "TE": pos("TE", [{"receptions": 4.0, "receiving_yards": 40.0, "targets": 6.0, "receiving_tds": 1.0}]),
    }
    r = bp.base_rates(qual)["stat_ratios"]
    assert r["receptions"]["receiving_yards"] == {"QB": None, "RB": 0.1, "WR": 0.08333, "TE": 0.1}
    assert r["receiving_yards"]["receptions"]["WR"] == 12.0
    assert r["rushing_yards"]["carries"] == {"QB": 4.0, "RB": 4.0, "WR": None, "TE": None}
    assert r["passing_tds"]["passing_yards"]["QB"] == 0.005
    assert r["passing_interceptions"]["passing_yards"]["QB"] == 0.0025
    assert r["passing_yards"]["passing_tds"]["QB"] == 200.0
    # TDs by component: receiving TDs per receiving yard, rushing TDs per rushing yard.
    assert r["rush_rec_tds"]["receiving_yards"]["WR"] == pytest.approx(1 / 60, abs=1e-4)
    assert r["rush_rec_tds"]["rushing_yards"]["RB"] == 0.0125
    assert r["rush_rec_tds"]["receiving_yards"]["RB"] == 0.0  # his TD was rushing
    assert set(r["rush_rec_tds"]) == {"rushing_yards", "receiving_yards"}
    # Rushing yards per rushing TD (S5.9: from the player's expected rushing TDs).
    assert r["rushing_yards"]["rushing_tds"]["QB"] == 40.0


# ---------------------------------------------------------------- dispersion


def test_implied_team_totals():
    sch = pl.DataFrame({"game_id": ["g"], "home_team": ["H"], "away_team": ["A"], "total_line": [48.0], "spread_line": [6.0]})
    out = {r["team"]: r["implied_total"] for r in bp.implied_team_totals(sch).to_dicts()}
    assert out == {"H": 27.0, "A": 21.0}


def test_proxy_features_use_only_earlier_games():
    rows = [{"player_id": "W", "position": "WR", "season": 2023, "week": w, "game_id": f"g{w}", "team": "AAA",
             "receiving_yards": float(10 * w), "targets": float(w), "offense_pct": 0.5 + w / 100} for w in range(1, 8)]
    rows.append({"player_id": "W", "position": "WR", "season": 2024, "week": 1, "game_id": "n1", "team": "AAA",
                 "receiving_yards": 999.0, "targets": 9.0, "offense_pct": 0.9})
    totals = pl.DataFrame({"game_id": ["g5"], "team": ["AAA"], "implied_total": [24.0]})
    f = bp.proxy_features(stat_rows(rows), "receiving_yards", "targets", totals)
    assert f["week"].to_list()[:3] == [5, 6, 7]  # weeks 1-4 lack 4 earlier games
    g5 = f.row(0, named=True)
    assert g5["trail4"] == pytest.approx(25.0)  # weeks 1-4: 10, 20, 30, 40
    assert g5["trail8"] == pytest.approx(25.0)
    assert g5["trail4_usage"] == pytest.approx(2.5)
    assert g5["season_to_date"] == pytest.approx(25.0)
    assert g5["trail_snap"] == pytest.approx(0.525)
    assert g5["implied_total"] == 24.0
    new_season = f.filter(pl.col("season") == 2024).row(0, named=True)
    assert new_season["trail8"] == pytest.approx(40.0)  # weeks 1-7 of 2023, not the 999 itself
    assert new_season["season_to_date"] == pytest.approx(40.0)  # no 2024 games yet: falls back to trail8


def test_ols_leave_one_season_out_predicts_held_out_season():
    rng = np.random.default_rng(6)
    n = 300
    x = rng.normal(size=n)
    df = pl.DataFrame({"season": np.repeat([2019, 2020, 2021], 100), "x": x, "y": 3.0 + 2.0 * x})
    np.testing.assert_allclose(bp.ols_leave_one_season_out(df, "y", ["x"]), df["y"].to_numpy(), atol=1e-9)


def test_fit_pi0_recovers_logistic():
    rng = np.random.default_rng(7)
    mu = rng.uniform(10, 90, 40_000)
    zero = rng.random(40_000) < special.expit(6.75 - 2.48 * np.log(mu))
    y = np.where(zero, 0.0, 30.0)
    c0, c1 = bp.fit_pi0(mu, y)
    assert c0 == pytest.approx(6.75, abs=0.4) and c1 == pytest.approx(-2.48, abs=0.1)


def test_fit_sd_relation_recovers_power_law():
    rng = np.random.default_rng(8)
    c0, c1, a, b, shift = 6.75, -2.48, 3.0, 0.62, 5.0
    mu = rng.uniform(20, 90, 20_000)
    m = mu / (1 - special.expit(c0 + c1 * np.log(mu)))
    sd, mean = a * m ** b, m + shift
    y = np.round(rng.gamma((mean / sd) ** 2, sd * sd / mean) - shift)
    y = np.where(y == 0, 1.0, y)  # the nonzero part only
    fa, fb = bp.fit_sd_relation(mu, y, c0, c1, shift)
    assert fa * 60 ** fb == pytest.approx(a * 60 ** b, rel=0.03)
    assert fb == pytest.approx(b, abs=0.06)


def test_fit_nb_relation_recovers_size_law():
    rng = np.random.default_rng(9)
    mu = rng.uniform(2, 7, 30_000)
    r = 0.83 * mu ** 1.85
    y = rng.negative_binomial(r, r / (r + mu)).astype(float)
    r_a, r_b = bp.fit_nb_relation(mu, y)
    assert r_a * 4 ** r_b == pytest.approx(0.83 * 4 ** 1.85, rel=0.25)
    assert r_b == pytest.approx(1.85, abs=0.4)


def test_existing_shifts_reads_committed_dispersion(tmp_path):
    assert bp.existing_shifts(tmp_path) == bp.DEFAULT_SHIFT
    assert bp.DEFAULT_SHIFT["rushing_yards"] == {"QB": 7.0, "RB": 5.0, "WR": 5.0, "TE": 5.0}  # S5.6 and its review
    (tmp_path / "dispersion.json").write_text(json.dumps({
        "receiving_yards": {"WR": {"shift": 4.1}},
        "rushing_yards": {"QB": {"shift": 8.0}},
        "passing_yards": {"QB": {"family": "hurdle_normal"}},  # no shift: a normal has none
    }))
    shifts = bp.existing_shifts(tmp_path)
    assert shifts["receiving_yards"] == {**bp.DEFAULT_SHIFT["receiving_yards"], "WR": 4.1}
    assert shifts["rushing_yards"] == {**bp.DEFAULT_SHIFT["rushing_yards"], "QB": 8.0}
    assert set(shifts) == {"receiving_yards", "rushing_yards"}


# ---------------------------------------------------------------- live


def _keys(doc, prefix=""):
    out = set()
    for k, v in doc.items():
        out.add(prefix + k)
        if isinstance(v, dict):
            out |= _keys(v, prefix + k + ".")
    return out


@pytest.mark.live
def test_build_from_nflverse_keeps_every_committed_key(tmp_path):
    (tmp_path / "dispersion.json").write_text((ROOT / "src/ffp/params/dispersion.json").read_text())
    assert bp.main(["--out", str(tmp_path), "--draws", "5000"]) == 0
    for name in ("dispersion", "correlation", "base_rates"):
        committed = json.loads((ROOT / "src/ffp/params" / f"{name}.json").read_text())
        built = json.loads((tmp_path / f"{name}.json").read_text())
        assert _keys(committed) <= _keys(built)


def test_fit_sd_relation_recovers_the_runtime_models_power_law():
    # Sprint 3 review finding 5: the fit put the gamma's location at m = mu / (1 - pi0), but
    # ffp.distributions.receiving_yards solves the location so the *zero-truncated* nonzero
    # mean is mu / (1 - pi0). Samples from the runtime model must give back its (a, b).
    # Before the fix a TE's sd came out ~6% too wide at m = 10 and ~3% at m = 20.
    from ffp.distributions import receiving_yards

    rng = np.random.default_rng(10)
    c0, c1, a, b, shift = 7.27, -2.79, 1.975, 0.716, 5.0  # TE, regenerated params
    mus, ys = [], []
    for mu in np.linspace(10, 50, 25):  # low mu, where the truncated zero bin is biggest
        target = mu / (1 - special.expit(c0 + c1 * np.log(mu)))
        nonzero = receiving_yards(target, a * target**b, "TE", pi0=0.0)  # the runtime nonzero part
        y = nonzero.ppf(rng.uniform(size=1600))
        mus.append(np.full(y.size, mu))
        ys.append(y)
    fa, fb = bp.fit_sd_relation(np.concatenate(mus), np.concatenate(ys), c0, c1, shift)
    for m in (10.0, 20.0, 40.0):
        assert fa * m**fb == pytest.approx(a * m**b, rel=0.01), m


@pytest.mark.parametrize(
    "target, sd, rel",
    [
        (np.array([8.0, 15.0, 30.0, 70.0]), 1.975 * np.array([8.0, 15.0, 30.0, 70.0]) ** 0.716, 1e-4),
        # Review finding 6: small targets, wide sd. The gamma is steep at its lower end here, so the
        # continuous mean the build uses and the runtime's discretised mean differ most (≈0.2%).
        (np.array([3.0, 5.0]), np.array([7.5, 9.0]), 5e-3),
    ],
)
def test_the_location_solver_matches_the_runtime_model(target, sd, rel):
    from ffp.distributions import receiving_yards

    m = bp._location_for_nonzero_mean(target, sd, 5.0)
    for t, s_, loc in zip(target, sd, m):
        assert loc == pytest.approx(receiving_yards(t, s_, "TE", pi0=0.0).m, rel=rel)


def test_the_location_solver_raises_where_it_cannot_converge():
    with pytest.raises(ValueError, match="did not converge"):
        bp._location_for_nonzero_mean(np.array([30.0]), np.array([20.0]), 5.0, max_iter=1)


# ---------------------------------------------------------------- Sprint 4: rushing, passing, carries


def _discretised_truncated_normal(m, sd, lo, hi):
    """(support, pmf) of the runtime passing-yards nonzero part, by brute force."""
    ys = np.arange(lo, hi + 1)
    p = special.ndtr((ys + 0.5 - m) / sd) - special.ndtr((ys - 0.5 - m) / sd)
    p[ys == 0] = 0.0
    return ys, p / p.sum()


@pytest.mark.parametrize("target, sd", [(250.0, 80.0), (150.0, 95.0), (3.0, 4.0)])
def test_normal_location_gives_the_discretised_truncated_mean(target, sd):
    m = bp._normal_location_for_nonzero_mean(np.array([target]), np.array([sd]))[0]
    ys, p = _discretised_truncated_normal(m, sd, math.floor(m - 15 * sd), math.ceil(m + 15 * sd))
    assert float(ys @ p) == pytest.approx(target, rel=1e-9)


def test_fit_sd_relation_recovers_the_normal_power_law():
    # QB passing yards: zero-truncated discretised normal, research table a = 514, b = -0.34.
    rng = np.random.default_rng(11)
    c0, c1, a, b = 15.88, -3.70, 514.0, -0.34
    mu = rng.uniform(150, 320, 20_000)
    target = mu / (1 - special.expit(c0 + c1 * np.log(mu)))
    sd = a * target**b
    m = bp._normal_location_for_nonzero_mean(target, sd)
    y = np.round(rng.normal(m, sd))
    while (y == 0).any():  # zero-truncate by redrawing
        redo = y == 0
        y[redo] = np.round(rng.normal(m[redo], sd[redo]))
    fa, fb = bp.fit_sd_relation(mu, y, c0, c1, 0.0, family="hurdle_normal")
    assert fa * 250**fb == pytest.approx(a * 250**b, rel=0.03)
    assert fb == pytest.approx(b, abs=0.12)


def test_gamma_nll_survives_kneels_below_the_shift():
    # A QB's -12-yard kneel game sits below the gamma's support (-shift = -10): the pmf floor
    # keeps the likelihood finite, and the floored game doesn't move the fit.
    params = [math.log(2.5), 0.64]
    target = np.array([20.0, 20.0])
    nll = bp._hurdle_nonzero_nll(params, target, np.array([15.0, -12.0]), 10.0, "hurdle_shifted_gamma")
    one = bp._hurdle_nonzero_nll(params, target[:1], np.array([15.0]), 10.0, "hurdle_shifted_gamma")
    assert math.isfinite(nll)
    assert nll - one == pytest.approx(-math.log(bp.PMF_FLOOR), abs=0.05)  # floor / (1 - zero bin)


def _synthetic_games(seed=12):
    """Three seasons of every position with every stat a relation needs (no network)."""
    rng = np.random.default_rng(seed)
    rows = []
    for pos in bp.SKILL:
        for k in range(14):
            level = rng.uniform(0.6, 1.4)
            for season in (2019, 2020, 2021):
                for week in range(1, 18):
                    tg = rng.poisson(7 * level)
                    rec = rng.binomial(tg, 0.65)
                    car = rng.poisson((4 if pos in ("QB", "WR", "TE") else 14) * level)
                    att = rng.poisson(33 * level) if pos == "QB" and k < 11 else rng.poisson(3)
                    rows.append({
                        "player_id": f"{pos}{k}", "position": pos, "season": season, "week": week,
                        "game_id": f"{season}_{week}_{pos}{k}", "team": "AAA", "offense_pct": 0.8,
                        "targets": float(tg), "receptions": float(rec),
                        "receiving_yards": float(rec and max(1, round(rng.gamma(2.0 * rec, 5.0)))),
                        "carries": float(car),
                        "rushing_yards": float(car and round(rng.gamma(1.5 * car, 3.0)) - rng.integers(0, 3)),
                        "attempts": float(att),
                        "passing_yards": float(att and max(1, round(rng.normal(7.2 * att, 50)))),
                    })
    return stat_rows(rows)


def _flat_totals(games):
    """Every game's implied team total at 22 points."""
    return games.select("game_id", "team").unique().with_columns(implied_total=pl.lit(22.0))


def test_proxy_sample_keeps_only_starting_qbs():
    rows = []
    for pid, att in (("A", 35.0), ("B", 10.0)):
        for season in (2020, 2021):
            for week in range(1, 9):
                rows.append({"player_id": pid, "position": "QB", "season": season, "week": week,
                             "game_id": f"{season}_{week}{pid}", "attempts": att + week % 3,
                             "passing_yards": 7.0 * att + week, "offense_pct": 1.0})
    games = stat_rows(rows)
    totals = _flat_totals(games)
    df = bp.proxy_sample(games, totals, "QB", "passing_yards", "attempts", -math.inf)
    assert df.height > 0 and set(df["player_id"]) == {"A"}  # B's trailing attempts are under 20


def test_dispersion_params_fits_every_sprint4_relation():
    games = _synthetic_games()
    totals = _flat_totals(games)
    out = bp.dispersion_params(games, totals, bp.DEFAULT_SHIFT)
    hurdle_keys = {"family", "shift", "pi0_c0", "pi0_c1", "sd_a", "sd_b"}
    for stat, positions in (("receiving_yards", {"WR", "TE", "RB"}), ("rushing_yards", {"QB", "RB", "WR", "TE"})):
        assert set(out[stat]) == positions
        for pos, entry in out[stat].items():
            assert set(entry) == hurdle_keys and entry["family"] == "hurdle_shifted_gamma"
            assert entry["shift"] == bp.DEFAULT_SHIFT[stat][pos]
    assert set(out["passing_yards"]) == {"QB"}
    assert out["passing_yards"]["QB"]["family"] == "hurdle_normal"
    assert set(out["passing_yards"]["QB"]) == hurdle_keys - {"shift"}
    for stat, positions in (("receptions", {"WR", "TE", "RB"}), ("carries", {"QB", "RB", "WR", "TE"})):
        assert set(out[stat]) == positions
        assert all(set(e) == {"family", "r_a", "r_b"} and e["family"] == "negative_binomial" for e in out[stat].values())
    # TE rushing is too thin to fit (7 TEs, 75 games in 2015-21): TEs borrow the WR relations,
    # and the borrowing is recorded instead of a sample size.
    assert out["borrowed"] == {"rushing_yards": {"TE": "WR"}, "carries": {"TE": "WR"}}
    for stat, borrowed in out["borrowed"].items():
        for pos, lender in borrowed.items():
            assert out[stat][pos] == out[stat][lender]
    for stat in ("receiving_yards", "receptions", "rushing_yards", "carries", "passing_yards"):
        assert set(out["n_games"][stat]) | set(out["borrowed"].get(stat, {})) == set(out[stat])
        assert not set(out["n_games"][stat]) & set(out["borrowed"].get(stat, {}))
        assert all(n > 0 for n in out["n_games"][stat].values())


def test_the_sd_relation_is_fitted_with_the_runtime_pi0(tmp_path):
    # Review finding 3: market_calibration.json overrides P(0) at runtime, so the sd relation of the
    # nonzero part (mean mu / (1 - pi0)) must be fitted with that pi0, not the nflverse one.
    rng = np.random.default_rng(3)
    mu = rng.uniform(10, 80, 4000)
    nonzero = rng.gamma(4.0, mu / 4.0) - 3.0
    y = np.where(rng.uniform(size=mu.size) < 0.05, 0.0, np.round(nonzero))
    own = bp.hurdle_entry(mu, y, bp.HURDLE_GAMMA, 3.0)
    calibrated = bp.hurdle_entry(mu, y, bp.HURDLE_GAMMA, 3.0, runtime_pi0=(4.0, -2.5))
    assert (calibrated["pi0_c0"], calibrated["pi0_c1"]) == (own["pi0_c0"], own["pi0_c1"])  # kept
    assert (calibrated["sd_a"], calibrated["sd_b"]) != (own["sd_a"], own["sd_b"])
    (tmp_path / "market_calibration.json").write_text(json.dumps({"pi0": {"rushing_yards": {"RB": {"c0": 4.0, "c1": -2.5}}}}))
    assert bp.calibrated_pi0(tmp_path) == {"rushing_yards": {"RB": {"c0": 4.0, "c1": -2.5}}}
    assert bp.calibrated_pi0(tmp_path / "nowhere") == {}


def test_per_carry_yards_by_position():
    # S5.10: one REG carry's yards, two-point tries and other positions left out; TE borrows WR.
    plays = pl.DataFrame({
        "season": [2020] * 7,
        "season_type": ["REG", "REG", "REG", "REG", "POST", "REG", "REG"],
        "rusher_player_id": ["rb", "rb", "rb", "rb", "rb", "rb", "wr"],
        "rushing_yards": [-2, 3, 3, 10, 50, 2, 8],
        "two_point_attempt": [0, 0, None, 0, 0, 1, 0],
    })
    players = pl.DataFrame({"gsis_id": ["rb", "wr"], "position": ["RB", "WR"]})
    out = bp.per_carry_params(plays, players)
    assert out["RB"] == {"lo": -2, "pmf": [0.25, 0, 0, 0, 0, 0.5, 0, 0, 0, 0, 0, 0, 0.25], "n": 4, "mean": 3.5}
    assert out["TE"] == out["WR"] == {"lo": 8, "pmf": [1.0], "n": 1, "mean": 8.0}
