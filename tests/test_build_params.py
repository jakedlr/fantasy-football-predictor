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
                          "passing_2pt_conversions": 1.0, "rushing_tds": 1.0}, {"carries": 2.0}]),
        "RB": pos("RB", [{"carries": 18.0, "receptions": 2.0, "fumbles_lost_total": 1.0, "rushing_tds": 3.0,
                          "receiving_tds": 1.0, "rushing_2pt_conversions": 1.0}]),
        "WR": pos("WR", [{"carries": 1.0, "receptions": 4.0, "fumbles_lost_total": 1.0, "receiving_tds": 1.0},
                         {"receptions": 5.0, "receiving_2pt_conversions": 1.0}]),
        "TE": pos("TE", [{"receptions": 4.0, "receiving_tds": 2.0}]),
    }
    r = bp.base_rates(qual)
    assert r["fumbles_lost_per_touch"] == {"RB": 0.05, "WR": 0.1, "TE": 0.0}
    assert r["qb_fumbles_lost_per_sack"] == 0.25
    assert r["fumbles_lost_per_game"] == {"QB": 0.5, "RB": 1.0, "WR": 0.5, "TE": 0.0}
    assert r["rush_rec_2pt_per_game"] == {"QB": 0.0, "RB": 1.0, "WR": 0.5, "TE": 0.0}
    assert r["pass_2pt_per_game"] == {"QB": 0.5}
    assert r["td_rushing_share"] == {"QB": 1.0, "RB": 0.75, "WR": 0.0, "TE": 0.0}
    assert r["carries_per_game"] == {"WR": 0.5, "TE": 0.0}
    assert r["n_games"] == {"QB": 2, "RB": 1, "WR": 2, "TE": 1}


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
    (tmp_path / "dispersion.json").write_text(json.dumps({"receiving_yards": {"WR": {"shift": 4.1}}}))
    assert bp.existing_shifts(tmp_path) == {**bp.DEFAULT_SHIFT, "WR": 4.1}


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
