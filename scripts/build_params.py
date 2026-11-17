#!/usr/bin/env python
"""Regenerate src/ffp/params/{dispersion,correlation,base_rates}.json from nflverse.

    python scripts/build_params.py [--out src/ffp/params] [--seasons 2021-2025]
                                   [--dispersion-seasons 2015-2021] [--draws 40000]

Methods follow research/nflverse-data.md (section 3: correlations; section 4: Base
Rates) and research/stat-distributions.md (Q2/Q4: dispersion relations). The pieces
are small pure functions (polars/numpy in, numbers out) so tests can run them on
synthetic frames; only `load_nflverse` touches the network (nflreadpy, cached on disk).
Data: nflverse, CC-BY 4.0.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
import polars as pl
from scipy import optimize, special, stats

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "src" / "ffp" / "params"
SKILL = ("QB", "RB", "WR", "TE")

# Copula dimensions per position, named by ffp.scoring.SCORING_STATS keys (nflverse
# player_stats uses the same column names for these).
CORRELATION_STATS = {
    "QB": ["passing_yards", "passing_tds", "passing_interceptions", "rushing_yards", "rushing_tds"],
    "RB": ["rushing_yards", "rushing_tds", "receptions", "receiving_yards", "receiving_tds"],
    "WR": ["receptions", "receiving_yards", "receiving_tds", "rushing_yards"],
    "TE": ["receptions", "receiving_yards", "receiving_tds", "rushing_yards"],
}
# "Meaningful usage" filters (nflverse-data.md 3, "Sample and filters").
QUALIFY = {"QB": ("attempts", 15), "RB": ("offense_pct", 0.30), "WR": ("offense_pct", 0.50), "TE": ("offense_pct", 0.50)}
WITHIN_MIN_GAMES = 6
# Estimated Stats (S5.4): a core stat with no Prop Market gets its mean from another stat's
# fitted mean times the position's pooled ratio between the two. stat -> the stats it can come from.
ESTIMATE_SOURCES = {
    "receptions": ("receiving_yards",),
    "receiving_yards": ("receptions",),
    "rushing_yards": ("carries", "rush_rec_tds"),
    "passing_yards": ("passing_tds",),
    "passing_tds": ("passing_yards",),
    "passing_interceptions": ("passing_yards",),
    "rush_rec_tds": ("rushing_yards", "receiving_yards"),  # summed: rushing TDs per rushing yard + receiving TDs per receiving yard
}
# The part of a stat a source predicts, where that is not the whole stat.
ESTIMATE_COMPONENT = {("rush_rec_tds", "rushing_yards"): "rushing_tds", ("rush_rec_tds", "receiving_yards"): "receiving_tds"}
STAT_EXPR = {
    **{c: pl.col(c) for c in ("receptions", "receiving_yards", "rushing_yards", "carries", "passing_yards",
                              "passing_tds", "passing_interceptions")},
    "rush_rec_tds": pl.col("rushing_tds") + pl.col("receiving_tds"),
}

# Per-player rushing TD share (nflverse-data.md 6, S4.3): kappa pseudo expected-TDs shrink the
# volume share toward m, k pseudo-TDs shrink the TD history toward the volume share, over this
# season to date + `prior_seasons` previous seasons.
TD_SHARE_SHRINKAGE = {"kappa": 2, "k": 30, "prior_seasons": 2}
PD_FLOOR = 1e-3

# Dispersion (stat-distributions.md). Families, as ffp.distributions implements them:
HURDLE_GAMMA = "hurdle_shifted_gamma"  # P(0) mass + discretised zero-truncated shifted gamma
HURDLE_NORMAL = "hurdle_normal"  # P(0) mass + discretised zero-truncated normal (no shift)
NEG_BIN = "negative_binomial"  # NB(mean mu, size r = r_a * mu^r_b)


class Relation(NamedTuple):
    """One mean-dispersion relation to fit: a stat, the usage stat its proxy mu trails
    (targets, carries or pass attempts), its family, per position the prop-eligible floor
    on the pre-game proxy mu (the positions to fit are this dict's keys), and the positions
    too thin to fit that borrow another position's fitted relation ({borrower: lender})."""

    stat: str
    usage: str
    family: str
    min_mu: dict[str, float]
    borrow: dict[str, str] = {}


# Floors: research's prop-eligible floors for receiving (stat-distributions.md "Sample").
# Rushing and carries go lower than research's RB 25-yard floor to cover the backup RBs and
# pocket QBs books also price (RB2 rush lines ~15-25, pocket-QB lines ~2-10). WR rushing:
# mu >= 5 yards (gadget receivers), 426 games of 38 WRs in 2015-21, stable under a
# player bootstrap and clearly narrower than the RB relation. WR carries: mu >= 0.5 (at
# >= 1 only 298 games and an unstable r). TE rushing is too thin to fit (7 TEs, 75 games at
# mu >= 5, mostly one gadget QB; a bootstrap's 90% band for P(0) at mu 15 is 1%-79%),
# so TEs borrow the WR relations, which their sample resembles (P(0) and sd at mu 10-25).
RELATIONS = (
    Relation("receiving_yards", "targets", HURDLE_GAMMA, {"WR": 20.0, "TE": 20.0, "RB": 10.0}),
    Relation("receptions", "targets", NEG_BIN, {"WR": 2.0, "TE": 2.0, "RB": 1.5}),
    Relation("rushing_yards", "carries", HURDLE_GAMMA, {"RB": 15.0, "QB": 2.0, "WR": 5.0}, {"TE": "WR"}),
    Relation("carries", "carries", NEG_BIN, {"RB": 4.0, "QB": 1.0, "WR": 0.5}, {"TE": "WR"}),
    Relation("passing_yards", "attempts", HURDLE_NORMAL, {"QB": 150.0}),
)
# Every QB relation is fit on starters only: trailing-4-game mean pass attempts >= 20
# (stat-distributions.md "Sample": QB trailing attempts >= 20).
TRAILING_QUALIFY = {"QB": ("attempts", 20.0)}
# The shifted gamma's shift is an input, not fitted (research Q1a: 5 for WR/TE receiving,
# 10 for RB receiving). A committed dispersion.json overrides these.
DEFAULT_SHIFT = {
    "receiving_yards": {"WR": 5.0, "TE": 5.0, "RB": 10.0},
    # Rushing: 5 for RB/WR/TE and 7 for QB (kneel-downs) since the Kalshi Backtest (S5.6): with 10,
    # low-volume backs got 7.7% below zero against 1.4% observed, and no RB reached -5. Not lower:
    # the support starts at -shift, and a -4 game must stay possible (review of PR #8).
    "rushing_yards": {"QB": 7.0, "RB": 5.0, "WR": 5.0, "TE": 5.0},
}
PROXY_FEATURES = ["trail8", "trail4", "trail8_usage", "trail4_usage", "season_to_date", "trail_snap", "implied_total"]
PMF_FLOOR = 1e-6

STAT_COLUMNS = [
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions", "sacks_suffered",
    "sack_fumbles_lost", "passing_2pt_conversions", "carries", "rushing_yards", "rushing_tds",
    "rushing_fumbles_lost", "rushing_2pt_conversions", "receptions", "targets", "receiving_yards",
    "receiving_tds", "receiving_fumbles_lost", "receiving_2pt_conversions", "fumbles_lost_total",
]


# ---------------------------------------------------------------- data


@dataclass
class Raw:
    player_stats: pl.DataFrame
    snap_counts: pl.DataFrame
    players: pl.DataFrame
    schedules: pl.DataFrame


def parse_seasons(text: str) -> list[int]:
    """'2021-2025' or '2021,2023' -> sorted list of seasons."""
    out: set[int] = set()
    for part in text.split(","):
        lo, _, hi = part.strip().partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return sorted(out)


def load_nflverse(seasons: list[int]) -> Raw:  # pragma: no cover - network
    import nflreadpy as nfl

    nfl.config.update_config(cache_mode="filesystem")
    return Raw(
        nfl.load_player_stats(seasons),
        nfl.load_snap_counts(seasons),
        nfl.load_players(),
        nfl.load_schedules(seasons),
    )


def skill_games(player_stats: pl.DataFrame, snap_counts: pl.DataFrame, players: pl.DataFrame,
                *, recover_zeros: bool = False) -> pl.DataFrame:
    """One row per QB/RB/WR/TE player-game with the stat columns and offense_pct.

    Snap share joins through players.pfr_id (pfr_player_id -> gsis_id). With
    `recover_zeros`, a game the player took offensive snaps in but that has no
    player_stats row (nflverse only writes rows with a stat) comes back as a zero line,
    positioned by players.position (stat-distributions.md "Data and method").
    """
    ids = (players.filter(pl.col("pfr_id").is_not_null())
           .select(pl.col("pfr_id").alias("pfr_player_id"), pl.col("gsis_id"), pl.col("position").alias("master_position")))
    snaps = (snap_counts.join(ids, on="pfr_player_id", how="inner")
             .group_by("gsis_id", "game_id")
             .agg(pl.col("offense_pct").max(), pl.col("offense_snaps").max(), pl.col("master_position").first(),
                  pl.col("season").first(), pl.col("week").first(), pl.col("game_type").first(), pl.col("team").first()))
    games = (player_stats.filter(pl.col("position").is_in(SKILL))
             .select("player_id", "position", "season", "week", "season_type", "game_id", "team", *STAT_COLUMNS)
             .with_columns(pl.col(STAT_COLUMNS).fill_null(0).cast(pl.Float64))
             .join(snaps.select(pl.col("gsis_id").alias("player_id"), "game_id", "offense_pct"),
                   on=["player_id", "game_id"], how="left"))
    if recover_zeros:
        zeros = (snaps.filter(pl.col("offense_snaps") > 0, pl.col("master_position").is_in(SKILL))
                 .join(games.select("player_id", "game_id"), left_on=["gsis_id", "game_id"],
                       right_on=["player_id", "game_id"], how="anti")
                 .select(pl.col("gsis_id").alias("player_id"), pl.col("master_position").alias("position"),
                         pl.col("season").cast(games.schema["season"]), pl.col("week").cast(games.schema["week"]),
                         pl.when(pl.col("game_type") == "REG").then(pl.lit("REG")).otherwise(pl.lit("POST")).alias("season_type"),
                         "game_id", "team", *[pl.lit(0.0).alias(c) for c in STAT_COLUMNS], "offense_pct"))
        games = pl.concat([games, zeros.select(games.columns)], how="vertical_relaxed")
    return games.sort("player_id", "season", "week")


def qualifying_games(games: pl.DataFrame, position: str) -> pl.DataFrame:
    """REG-season games of one position that pass the usage filter."""
    col, floor = QUALIFY[position]
    return games.filter(pl.col("season_type") == "REG", pl.col("position") == position, pl.col(col) >= floor)


# ---------------------------------------------------------------- correlation


def _correlation_about_zero(centred: np.ndarray) -> np.ndarray:
    """Correlation of already-centred columns; a constant column correlates 0 with the rest."""
    sums = centred.T @ centred
    d = np.sqrt(np.diag(sums))
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.nan_to_num(sums / np.outer(d, d))
    np.fill_diagonal(r, 1.0)
    return r


def spearman_matrix(df: pl.DataFrame, cols: list[str]) -> np.ndarray:
    """Pooled Spearman (average ranks for ties) across all rows (nflverse-data.md 3a)."""
    ranks = np.column_stack([stats.rankdata(df[c].to_numpy()) for c in cols])
    return _correlation_about_zero(ranks - ranks.mean(axis=0))


def within_player_sample(df: pl.DataFrame, min_games: int = WITHIN_MIN_GAMES) -> pl.DataFrame:
    """Player-seasons with at least `min_games` qualifying games."""
    return df.filter(pl.len().over("player_id", "season") >= min_games)


def within_spearman_matrix(df: pl.DataFrame, cols: list[str]) -> np.ndarray:
    """Within-player-season Spearman (3b): rank each stat inside each player-season,
    centre the ranks on the player-season mean, correlate the pooled centred ranks."""
    g = ["player_id", "season"]
    centred = df.select([(pl.col(c).rank("average").over(g) - pl.col(c).rank("average").mean().over(g)).alias(c)
                         for c in cols]).to_numpy()
    return _correlation_about_zero(centred)


def _step_quantile(sorted_values: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Empirical step-CDF quantile x = min{v : F(v) >= u}."""
    n = len(sorted_values)
    return sorted_values[np.clip(np.ceil(u * n).astype(int) - 1, 0, n - 1)]


def simulated_spearman(x: np.ndarray, y: np.ndarray, rho: float, z: np.ndarray) -> float:
    """Spearman of a Gaussian copula with latent rho and empirical margins x, y.
    `z` is an (n, 2) array of iid standard normals (common random numbers)."""
    z2 = rho * z[:, 0] + np.sqrt(1.0 - rho * rho) * z[:, 1]
    a = _step_quantile(np.sort(x), special.ndtr(z[:, 0]))
    b = _step_quantile(np.sort(y), special.ndtr(z2))
    if a.min() == a.max() or b.min() == b.max():
        return 0.0
    return float(np.corrcoef(stats.rankdata(a), stats.rankdata(b))[0, 1])


def latent_rho(x: np.ndarray, y: np.ndarray, target: float, z: np.ndarray, tol: float = 5e-4) -> float:
    """Bisection for the latent rho whose simulated Spearman equals `target` (3c)."""
    lo, hi = -0.999, 0.999
    if target <= simulated_spearman(x, y, lo, z):
        return lo
    if target >= simulated_spearman(x, y, hi, z):
        return hi
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        if simulated_spearman(x, y, mid, z) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def latent_matrix(df: pl.DataFrame, cols: list[str], target: np.ndarray, *, draws: int = 40_000, seed: int = 0) -> np.ndarray:
    """Latent Gaussian-copula R reproducing `target` Spearman under df's pooled margins."""
    z = np.random.default_rng(seed).standard_normal((draws, 2))
    k = len(cols)
    r = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            r[i, j] = r[j, i] = latent_rho(df[cols[i]].to_numpy(), df[cols[j]].to_numpy(), target[i, j], z)
    return r


def make_positive_definite(r: np.ndarray, floor: float = PD_FLOOR) -> np.ndarray:
    """Clip eigenvalues at `floor` and rescale to a unit diagonal (no-op if already PD)."""
    r = (r + r.T) / 2
    w, v = np.linalg.eigh(r)
    if w.min() >= floor:
        return r
    fixed = v @ np.diag(np.maximum(w, floor)) @ v.T
    d = np.sqrt(np.diag(fixed))
    return fixed / np.outer(d, d)


def rounded(r: np.ndarray, digits: int = 3) -> list[list[float]]:
    out = np.round(r, digits)
    np.fill_diagonal(out, 1.0)
    return [[float(v) + 0.0 for v in row] for row in out]  # + 0.0 turns -0.0 into 0.0


def correlation_entry(df: pl.DataFrame, cols: list[str], kind: str, *, draws: int, seed: int) -> dict:
    if kind == "within":
        df = within_player_sample(df)
        observed = within_spearman_matrix(df, cols)
    else:
        observed = spearman_matrix(df, cols)
    latent = make_positive_definite(latent_matrix(df, cols, observed, draws=draws, seed=seed))
    return {
        "stats": list(cols),
        "matrix": rounded(latent),
        "spearman": rounded(observed),
        "n_games": df.height,
        "n_players": df["player_id"].n_unique(),
        "n_player_seasons": df.select("player_id", "season").n_unique(),
        "min_eigenvalue": round(float(np.linalg.eigvalsh(latent).min()), 3),
    }


# ---------------------------------------------------------------- base rates


def base_rates(qual: dict[str, pl.DataFrame]) -> dict:
    """Base Rates for the no-market Scoring Stats (nflverse-data.md 4)."""
    def total(pos: str, expr: pl.Expr) -> float:
        return float(qual[pos].select(expr.sum()).item())

    def ratio(pos: str, num: str, den: str) -> float | None:
        """Pooled num / den, None when the position has no den (e.g. no TE carries)."""
        d = total(pos, pl.col(den))
        return round(total(pos, pl.col(num)) / d, 4) if d else None

    def stat_ratio(pos: str, stat: str, src: str) -> float | None:
        """Pooled stat / src (S5.4), e.g. receptions per receiving yard; None with no src."""
        d = total(pos, STAT_EXPR[src])
        num = ESTIMATE_COMPONENT.get((stat, src))
        return float(f"{total(pos, pl.col(num) if num else STAT_EXPR[stat]) / d:.4g}") if d else None

    touches = pl.col("carries") + pl.col("receptions")
    rush_rec_2pt = pl.col("rushing_2pt_conversions") + pl.col("receiving_2pt_conversions")
    n = {p: qual[p].height for p in qual}
    return {
        "fumbles_lost_per_touch": {p: round(total(p, pl.col("fumbles_lost_total")) / total(p, touches), 4) for p in ("RB", "WR", "TE")},
        "qb_fumbles_lost_per_sack": round(total("QB", pl.col("sack_fumbles_lost")) / total("QB", pl.col("sacks_suffered")), 3),
        "fumbles_lost_per_game": {p: round(total(p, pl.col("fumbles_lost_total")) / n[p], 3) for p in SKILL},
        "rush_rec_2pt_per_game": {p: round(total(p, rush_rec_2pt) / n[p], 3) for p in SKILL},
        "pass_2pt_per_game": {"QB": round(total("QB", pl.col("passing_2pt_conversions")) / n["QB"], 3)},
        "td_rushing_share": {p: round(total(p, pl.col("rushing_tds")) / total(p, pl.col("rushing_tds") + pl.col("receiving_tds")), 3) for p in SKILL},
        "carries_per_game": {p: round(total(p, pl.col("carries")) / n[p], 3) for p in ("WR", "TE")},
        "n_games": n,
        "qb_sacks_per_game": round(total("QB", pl.col("sacks_suffered")) / n["QB"], 3),
        "qb_sacks_per_attempt": round(total("QB", pl.col("sacks_suffered")) / total("QB", pl.col("attempts")), 4),
        "qb_fumbles_lost_per_carry": round(total("QB", pl.col("rushing_fumbles_lost")) / total("QB", pl.col("carries")), 4),
        "yards_per_carry": {p: (round(total(p, pl.col("rushing_yards")) / c, 3) if (c := total(p, pl.col("carries"))) else None)
                            for p in SKILL},
        "stat_ratios": {
            stat: {src: {p: stat_ratio(p, stat, src) for p in SKILL} for src in sources}
            for stat, sources in ESTIMATE_SOURCES.items()
        },
        "td_share": {
            "rho_rush": {p: ratio(p, "rushing_tds", "carries") for p in SKILL},
            "rho_rec": {p: ratio(p, "receiving_tds", "targets") for p in SKILL},
            **TD_SHARE_SHRINKAGE,
        },
    }


# ---------------------------------------------------------------- dispersion


def implied_team_totals(schedules: pl.DataFrame) -> pl.DataFrame:
    """(game_id, team, implied_total) from the closing total and spread (spread_line > 0: home favoured)."""
    sch = schedules.select("game_id", "home_team", "away_team", "total_line", "spread_line")
    home = sch.select("game_id", pl.col("home_team").alias("team"), ((pl.col("total_line") + pl.col("spread_line")) / 2).alias("implied_total"))
    away = sch.select("game_id", pl.col("away_team").alias("team"), ((pl.col("total_line") - pl.col("spread_line")) / 2).alias("implied_total"))
    return pl.concat([home, away])


def proxy_features(games: pl.DataFrame, stat: str, usage: str, totals: pl.DataFrame, min_prior: int = 4) -> pl.DataFrame:
    """Pre-game features from the player's earlier games only: trailing 8/4-game means of
    the stat and its usage, season-to-date mean, trailing snap share, implied team total.
    Rows with fewer than `min_prior` earlier games are dropped."""
    def trailing(col: str, k: int) -> pl.Expr:
        return pl.col(col).shift(1).rolling_mean(k, min_samples=min(k, min_prior)).over("player_id")

    ps = ["player_id", "season"]
    df = (games.sort("player_id", "season", "week")
          .with_columns(
              trail8=trailing(stat, 8), trail4=trailing(stat, 4),
              trail8_usage=trailing(usage, 8), trail4_usage=trailing(usage, 4),
              trail_snap=pl.col("offense_pct").shift(1).rolling_mean(4, min_samples=1).over("player_id"),
              season_to_date=((pl.col(stat).cum_sum() - pl.col(stat)) / (pl.int_range(pl.len()))).over(ps),
          )
          .join(totals, on=["game_id", "team"], how="left")
          .filter(pl.col("trail4").is_not_null()))
    return df.with_columns(
        pl.col("season_to_date").fill_nan(None).fill_null(pl.col("trail8")),
        pl.col("trail_snap").fill_null(pl.col("trail_snap").median()),
        pl.col("implied_total").fill_null(pl.col("implied_total").mean()),
    )


def ols_leave_one_season_out(df: pl.DataFrame, target: str, features: list[str]) -> np.ndarray:
    """Out-of-season OLS prediction for each row: fit on every other season, predict this one."""
    x = np.column_stack([np.ones(df.height), df.select(features).to_numpy()])
    y = df[target].to_numpy()
    season = df["season"].to_numpy()
    pred = np.empty(df.height)
    for s in np.unique(season):
        test = season == s
        beta, *_ = np.linalg.lstsq(x[~test], y[~test], rcond=None)
        pred[test] = x[test] @ beta
    return pred


def fit_pi0(mu: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Logistic MLE: P(y == 0) = expit(c0 + c1 ln mu)."""
    zero = (y == 0).astype(float)
    lm = np.log(mu)

    def nll(c):
        eta = c[0] + c[1] * lm
        return float(np.sum(np.logaddexp(0, eta) - zero * eta))

    res = optimize.minimize(nll, x0=[0.0, -1.0], method="BFGS")
    return float(res.x[0]), float(res.x[1])


def _location_for_nonzero_mean(target: np.ndarray, sd: np.ndarray, shift: float, max_iter: int = 100) -> np.ndarray:
    """The gamma location m whose zero-truncated nonzero part has mean `target`, solved exactly as
    ffp.distributions.receiving_yards solves it at runtime (the gamma on X + shift has mean
    m + shift and sd `sd`; the bin X in (-1/2, 1/2) is removed): the step m <- max(m - error, m/2),
    to the same 1e-11 relative tolerance. Vectorised, using E[G; G in A] = mean * P_{k+1}(A) for a
    gamma G. Raises ValueError where it doesn't converge, as the runtime raises FitError.

    One approximation remains: this is the continuous part's mean, the runtime takes the mean of
    the discretised bins. They differ by ~3e-5 relative at typical targets and by up to ~0.2% for
    tiny targets with a wide sd, where the gamma is steep at its lower end. An exact discrete mean
    needs a sum over the integer support per row, too slow inside the NLL."""
    target = np.asarray(target, dtype=float)
    m = target.copy()
    lo, hi = max(shift - 0.5, 0.0), shift + 0.5
    for _ in range(max_iter):
        mean = m + shift
        k, scale = (mean / sd) ** 2, sd * sd / mean
        zero_bin = special.gammainc(k, hi / scale) - special.gammainc(k, lo / scale)
        mass_in_bin = mean * (special.gammainc(k + 1, hi / scale) - special.gammainc(k + 1, lo / scale))
        err = (mean - mass_in_bin) / (1.0 - zero_bin) - shift - target
        done = np.abs(err) < 1e-11 * target
        if done.all():
            return m
        m = np.where(done, m, np.maximum(m - err, m / 2))
    raise ValueError(f"nonzero mean did not converge for {int((~done).sum())} of {done.size} targets")


def _normal_location_for_nonzero_mean(target: np.ndarray, sd: np.ndarray, max_iter: int = 100) -> np.ndarray:
    """The normal location m whose discretised, zero-truncated nonzero part has mean `target`.

    Discretising N(m, sd) into integer bins centred on the integers keeps the mean at m (the
    error is ~exp(-2 pi^2 sd^2), nil for sd >= 1), and the dropped bin y = 0 adds nothing to
    the sum, so the nonzero mean is m / (1 - Z), Z = P(-1/2 < X < 1/2). Solved by the
    fixed point m <- target (1 - Z(m)), exact for the discrete model (no approximation, unlike
    the gamma's continuous-part mean). Raises ValueError where it doesn't converge."""
    target = np.asarray(target, dtype=float)
    m = target.copy()
    for _ in range(max_iter):
        z = special.ndtr((0.5 - m) / sd) - special.ndtr((-0.5 - m) / sd)
        new = target * (1.0 - z)
        if np.all(np.abs(new - m) < 1e-11 * np.abs(target)):
            return new
        m = new
    raise ValueError(f"nonzero mean did not converge for {target.size} targets")


def _gamma_cdf(target: np.ndarray, sd: np.ndarray, shift: float):
    """cdf of the continuous nonzero part X = G - shift in yards, gamma G located as at runtime."""
    mean = _location_for_nonzero_mean(target, sd, shift) + shift
    k, scale = (mean / sd) ** 2, sd * sd / mean
    return lambda v: special.gammainc(k, np.maximum(v + shift, 0.0) / scale)


def _normal_cdf(target: np.ndarray, sd: np.ndarray, shift: float):
    """cdf of the continuous nonzero part X ~ N(m, sd), located as at runtime (shift unused)."""
    m = _normal_location_for_nonzero_mean(target, sd)
    return lambda v: special.ndtr((v - m) / sd)


NONZERO_CDF = {HURDLE_GAMMA: _gamma_cdf, HURDLE_NORMAL: _normal_cdf}
# Nelder-Mead start (ln a, b) per family: near the research relations (gamma b ~ 0.5-0.8;
# passing-yards sd nearly flat at ~80).
SD_START = {HURDLE_GAMMA: (math.log(3.0), 0.6), HURDLE_NORMAL: (math.log(80.0), 0.0)}


def _hurdle_nonzero_nll(params, target: np.ndarray, y: np.ndarray, shift: float, family: str = HURDLE_GAMMA) -> float:
    """Negative log-likelihood of nonzero integer yards under the runtime model: a zero-truncated,
    discretised continuous part X (shifted gamma or normal) with sd a * target^b, located so the
    truncated nonzero mean is `target` = mu / (1 - pi0) (params = ln a, b). P(y) is
    [F(y + 1/2) - F(y - 1/2)] / (1 - Z), Z the y = 0 bin, floored at PMF_FLOOR so a yards value
    outside the support (a QB kneel game below -shift) cannot break the likelihood."""
    a, b = np.exp(params[0]), params[1]
    sd = a * target ** b
    try:
        cdf = NONZERO_CDF[family](target, sd, shift)
    except ValueError:  # no runtime distribution exists here either: steer the optimiser away
        return math.inf
    p = cdf(y + 0.5) - cdf(y - 0.5)
    p0 = cdf(0.5) - cdf(-0.5)
    return float(-np.sum(np.log(np.maximum(p, PMF_FLOOR) / (1.0 - p0))))


def fit_sd_relation(mu: np.ndarray, y: np.ndarray, c0: float, c1: float, shift: float,
                    family: str = HURDLE_GAMMA) -> tuple[float, float]:
    """MLE of (a, b) in sd_nz = a * m^b for the nonzero part, m = mu / (1 - pi0(mu)) its mean,
    with the continuous part located as at runtime (see _hurdle_nonzero_nll)."""
    nz = y != 0
    m = mu[nz] / (1.0 - special.expit(c0 + c1 * np.log(mu[nz])))
    res = optimize.minimize(_hurdle_nonzero_nll, x0=list(SD_START[family]), args=(m, y[nz], shift, family),
                            method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-4, "maxiter": 2000})
    if not res.success:
        raise RuntimeError(f"sd relation fit did not converge: {res.message}")
    return float(np.exp(res.x[0])), float(res.x[1])


def fit_nb_relation(mu: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """MLE of (r_a, r_b) for y ~ NB(mean mu, size r = r_a * mu^r_b)."""
    def nll(params):
        r = np.exp(params[0]) * mu ** params[1]
        ll = (special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1)
              + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu)))
        return float(-np.sum(ll))

    res = optimize.minimize(nll, x0=[0.0, 1.0], method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-4, "maxiter": 2000})
    return float(np.exp(res.x[0])), float(res.x[1])


def proxy_sample(games: pl.DataFrame, totals: pl.DataFrame, position: str, stat: str, usage: str, min_mu: float) -> pl.DataFrame:
    """Prop-eligible games of a position with the leave-one-season-out proxy mu: mu >= min_mu,
    and for a position in TRAILING_QUALIFY (QB) a trailing-4-game mean of its column at or
    above the floor. The proxy is fit on all the position's games; the filters come after."""
    pos_games = games.filter(pl.col("position") == position).sort("player_id", "season", "week")
    qualify = TRAILING_QUALIFY.get(position)
    if qualify:
        pos_games = pos_games.with_columns(
            qualify_trail=pl.col(qualify[0]).shift(1).rolling_mean(4, min_samples=4).over("player_id"))
    df = proxy_features(pos_games, stat, usage, totals)
    df = df.with_columns(mu=pl.Series(ols_leave_one_season_out(df, stat, PROXY_FEATURES)))
    df = df.filter(pl.col("mu") >= min_mu)
    return df.filter(pl.col("qualify_trail") >= qualify[1]) if qualify else df


def hurdle_entry(mu: np.ndarray, y: np.ndarray, family: str, shift: float,
                 runtime_pi0: tuple[float, float] | None = None) -> dict:
    """pi0 and sd relations of a hurdle yards stat; a normal has no shift, so no "shift" key.

    The sd relation is of the nonzero part, whose mean is mu / (1 − pi0), so it must be fitted
    with the pi0 the runtime uses: `runtime_pi0` = (c0, c1) when params/market_calibration.json
    overrides this relation's P(0) (S5.6), else the one fitted here. The entry keeps the
    nflverse pi0 fit either way (the override lives in market_calibration.json)."""
    c0, c1 = fit_pi0(mu, y)
    a, b = fit_sd_relation(mu, y, *(runtime_pi0 or (c0, c1)), shift, family)
    head = {"family": family, "shift": shift} if family == HURDLE_GAMMA else {"family": family}
    return {**head, "pi0_c0": round(c0, 3), "pi0_c1": round(c1, 3), "sd_a": round(a, 3), "sd_b": round(b, 3)}


def nb_entry(mu: np.ndarray, y: np.ndarray) -> dict:
    r_a, r_b = fit_nb_relation(mu, y)
    return {"family": NEG_BIN, "r_a": round(r_a, 3), "r_b": round(r_b, 3)}


def dispersion_params(games: pl.DataFrame, totals: pl.DataFrame, shifts: dict[str, dict[str, float]],
                      relations: tuple[Relation, ...] = RELATIONS, runtime_pi0: dict | None = None) -> dict:
    """Fitted relations per stat and position; sample sizes go under a separate "n_games" key
    so each relation entry holds only its family and parameters. A borrowing position gets a
    copy of its lender's entry, listed under "borrowed" instead of "n_games"."""
    out: dict = {r.stat: {} for r in relations}
    out["n_games"] = {r.stat: {} for r in relations}
    out["borrowed"] = {r.stat: dict(r.borrow) for r in relations if r.borrow}
    for rel in relations:
        for pos, min_mu in rel.min_mu.items():
            df = proxy_sample(games, totals, pos, rel.stat, rel.usage, min_mu)
            mu, y = df["mu"].to_numpy(), df[rel.stat].to_numpy()
            if rel.family == NEG_BIN:
                out[rel.stat][pos] = nb_entry(mu, y)
            else:
                override = (runtime_pi0 or {}).get(rel.stat, {}).get(pos)
                out[rel.stat][pos] = hurdle_entry(mu, y, rel.family, shifts.get(rel.stat, {}).get(pos, 0.0),
                                                  None if override is None else (override["c0"], override["c1"]))
            out["n_games"][rel.stat][pos] = df.height
        for pos, lender in rel.borrow.items():
            out[rel.stat][pos] = dict(out[rel.stat][lender])
    return out


# ---------------------------------------------------------------- build


def calibrated_pi0(out_dir: Path) -> dict:
    """The P(0) relations params/market_calibration.json overrides at runtime (S5.6), by stat and
    position: the sd relations must be fitted with them. That file is kept by hand, not written here."""
    path = out_dir / "market_calibration.json"
    return json.loads(path.read_text()).get("pi0", {}) if path.exists() else {}


def existing_shifts(out_dir: Path) -> dict[str, dict[str, float]]:
    """Shifted-gamma shifts are an input: keep whatever the committed dispersion.json has,
    per stat and position, over DEFAULT_SHIFT."""
    shifts = {stat: dict(by_pos) for stat, by_pos in DEFAULT_SHIFT.items()}
    path = out_dir / "dispersion.json"
    if path.exists():
        for stat, table in json.loads(path.read_text()).items():
            if stat == "n_games" or not isinstance(table, dict):
                continue
            for pos, entry in table.items():
                if isinstance(entry, dict) and "shift" in entry:
                    shifts.setdefault(stat, {})[pos] = float(entry["shift"])
    return shifts


def season_label(seasons: list[int]) -> str:
    return f"{seasons[0]}-{seasons[-1]}" if seasons == list(range(seasons[0], seasons[-1] + 1)) else ",".join(map(str, seasons))


def build(raw: Raw, seasons: list[int], dispersion_seasons: list[int], shifts: dict[str, dict[str, float]], *, draws: int,
          today: str, runtime_pi0: dict | None = None) -> dict[str, dict]:
    stamp = f"scripts/build_params.py on {today} from nflverse (CC-BY 4.0) via nflreadpy"
    ps, snaps = raw.player_stats, raw.snap_counts

    games = skill_games(ps.filter(pl.col("season").is_in(seasons)), snaps.filter(pl.col("season").is_in(seasons)), raw.players)
    qual = {p: qualifying_games(games, p) for p in SKILL}
    correlation: dict = {
        "source": f"{stamp}; {season_label(seasons)} REG, nflverse-data.md section 3 filters",
        "note": ("matrix = latent Gaussian-copula rho found by bisection so that simulating with the sample's pooled "
                 "empirical margins reproduces the observed Spearman (spearman): within-player-season centred ranks "
                 f"(player-seasons with >= {WITHIN_MIN_GAMES} qualifying games) for 'within', pooled player-games for "
                 "'pooled'; eigenvalues clipped and rescaled if not positive definite"),
        "default": "within",
    }
    for kind in ("within", "pooled"):
        correlation[kind] = {p: correlation_entry(qual[p], CORRELATION_STATS[p], kind, draws=draws, seed=0) for p in SKILL}

    rates = base_rates(qual)
    base = {"source": f"{stamp}; {season_label(seasons)} REG qualifying games (nflverse-data.md section 4)", **rates,
            "carries_per_game_note": ("mean carries per qualifying game (WR/TE), for expected touches when there is no "
                                      "rushing market; QB/RB have no entry on purpose (see yards_per_carry)"),
            "yards_per_carry_note": ("pooled rushing yards / carries per position, to turn a rushing-yards mean into "
                                     "expected carries when there is no rush-attempts market (FanDuel has none)"),
            "qb_sacks_note": ("QB fumbles lost (S4.4) = qb_fumbles_lost_per_sack x expected sacks (qb_sacks_per_attempt x "
                              "expected pass attempts, else qb_sacks_per_game) + qb_fumbles_lost_per_carry x expected "
                              "carries; sacks_suffered and attempts from nflverse player_stats"),
            "td_share_note": ("per-player rushing TD share (ffp.td_share, nflverse-data.md section 6): v = (rho_rush*C + "
                              "kappa*m) / (rho_rush*C + rho_rec*T + kappa), share = (R + k*v) / (N + k), with m = "
                              "td_rushing_share, C/T/R/N = carries, targets, rushing TDs, rush+rec TDs over this season "
                              "to date + prior_seasons previous seasons (REG+POST); rho = TDs per carry / per target "
                              "in the qualifying games")}

    dgames = skill_games(ps.filter(pl.col("season").is_in(dispersion_seasons)),
                         snaps.filter(pl.col("season").is_in(dispersion_seasons)), raw.players, recover_zeros=True)
    dispersion = {"source": (f"{stamp}; fit on {season_label(dispersion_seasons)} REG+POST with zero-stat games recovered "
                             "from snap counts, mu = leave-one-season-out OLS proxy (stat-distributions.md Q2); "
                             "shifted-gamma shifts are inputs, not fitted; QB relations on starters (trailing "
                             "4-game pass attempts >= 20); see RELATIONS in the script for the per-position mu floors"),
                  **dispersion_params(dgames, implied_team_totals(raw.schedules), shifts, runtime_pi0=runtime_pi0)}
    return {"correlation": correlation, "base_rates": base, "dispersion": dispersion}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="directory for the JSON files (default: src/ffp/params)")
    ap.add_argument("--seasons", default="2021-2025", help="correlation and Base Rate seasons (default 2021-2025)")
    ap.add_argument("--dispersion-seasons", default="2015-2021", help="dispersion fit seasons (default 2015-2021)")
    ap.add_argument("--draws", type=int, default=40_000, help="simulation draws per latent-rho bisection")
    args = ap.parse_args(argv)

    seasons, dseasons = parse_seasons(args.seasons), parse_seasons(args.dispersion_seasons)
    raw = load_nflverse(sorted(set(seasons) | set(dseasons)))
    docs = build(raw, seasons, dseasons, existing_shifts(args.out), draws=args.draws, today=dt.date.today().isoformat(),
                 runtime_pi0=calibrated_pi0(args.out))
    args.out.mkdir(parents=True, exist_ok=True)
    for name, doc in docs.items():
        (args.out / f"{name}.json").write_text(json.dumps(doc, indent=2) + "\n")
        print(f"wrote {args.out / f'{name}.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
