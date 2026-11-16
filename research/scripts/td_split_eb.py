#!/usr/bin/env python
"""Rushing-vs-receiving TD share from a player's own history, shrunk toward the position share.

Research for PLAN.md S4.3; findings in research/nflverse-data.md ("Per-player rushing TD
share (empirical Bayes)"). Run from the repo root:

    .venv/bin/python research/scripts/td_split_eb.py            # EB toward the position share
    .venv/bin/python research/scripts/td_split_eb.py --volume   # volume-based prior (follow-up)
    .venv/bin/python research/scripts/td_split_eb.py --final    # recommended model, calibration, examples

Data: nflverse (CC-BY 4.0) weekly player stats + season rosters via nflreadpy, cached on disk.

Model: share_i = (R_i + k * m) / (N_i + k), where R_i = rushing TDs and N_i = rushing +
receiving TDs in the player's (weighted) history, m = the prior mean (the position share, or
a volume-based prior), k = the prior strength (Beta(k*m, k*(1-m))). Scored out of sample by
the log loss per TD of the held-out TDs' rushing/receiving labels.
"""

from __future__ import annotations

import itertools

import nflreadpy as nfl
import numpy as np
import polars as pl
from scipy import optimize, special

SKILL = ("QB", "RB", "WR", "TE")
FIRST, LAST = 2010, 2025          # data seasons loaded
TARGETS = list(range(2016, 2026))  # held-out seasons (>= 6 seasons of history before them)
TUNE = [s for s in TARGETS if s <= 2020]
TEST = [s for s in TARGETS if s >= 2021]
MAXLAG = 6
RNG = np.random.default_rng(0)


# ---------------------------------------------------------------- data


def load() -> pl.DataFrame:
    """One row per skill player-game (REG + POST) with the season's roster position."""
    nfl.config.update_config(cache_mode="filesystem")
    ps = nfl.load_player_stats(list(range(FIRST, LAST + 1)))
    ro = nfl.load_rosters(list(range(FIRST, LAST + 1)))
    pos = (ro.filter(pl.col("gsis_id").is_not_null())
           .with_columns(pl.col("position").replace({"FB": "RB", "HB": "RB"}))
           .group_by("gsis_id", "season").agg(pl.col("position").last().alias("roster_position")))
    g = (ps.select("player_id", "player_display_name", "position", "season", "week", "season_type",
                   "rushing_tds", "receiving_tds", "carries", "targets", "rushing_yards", "receiving_yards",
                   "rushing_2pt_conversions", "receiving_2pt_conversions")
         .join(pos, left_on=["player_id", "season"], right_on=["gsis_id", "season"], how="left")
         .with_columns(pl.when(pl.col("roster_position").is_in(SKILL)).then(pl.col("roster_position"))
                       .otherwise(pl.col("position")).alias("pos"))
         .filter(pl.col("pos").is_in(SKILL))
         .with_columns(pl.col("rushing_tds", "receiving_tds", "carries", "targets", "rushing_yards", "receiving_yards",
                              "rushing_2pt_conversions", "receiving_2pt_conversions").fill_null(0).cast(pl.Float64))
         .with_columns(r=pl.col("rushing_tds"), n=pl.col("rushing_tds") + pl.col("receiving_tds")))
    return g


# ---------------------------------------------------------------- beta-binomial EB


def bb_negloglik(params, r, n):
    a, b = np.exp(params)
    return -np.sum(special.betaln(r + a, n - r + b) - special.betaln(a, b))


def fit_beta(r: np.ndarray, n: np.ndarray) -> tuple[float, float]:
    """Marginal-likelihood Beta prior; returns (mean m, strength k = a + b)."""
    m0 = r.sum() / n.sum()
    res = optimize.minimize(bb_negloglik, np.log([m0 * 10, (1 - m0) * 10]), args=(r, n), method="Nelder-Mead",
                            options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000})
    a, b = np.exp(res.x)
    return a / (a + b), a + b


def eb_table(g: pl.DataFrame) -> None:
    print("\n== Beta-binomial marginal likelihood per position (player-seasons with >=1 TD, 2015-2025) ==")
    ps = (g.filter(pl.col("season") >= 2015).group_by("player_id", "season", "pos")
          .agg(pl.col("r").sum(), pl.col("n").sum()).filter(pl.col("n") > 0))
    for pos in SKILL:
        d = ps.filter(pl.col("pos") == pos)
        r, n = d["r"].to_numpy(), d["n"].to_numpy()
        m, k = fit_beta(r, n)
        ids = d["player_id"].to_numpy()
        uniq = np.unique(ids)
        idx = {u: np.where(ids == u)[0] for u in uniq}
        boots = []
        for _ in range(200):
            take = np.concatenate([idx[u] for u in RNG.choice(uniq, len(uniq))])
            boots.append(fit_beta(r[take], n[take]))
        bk = np.array([b[1] for b in boots])
        bm = np.array([b[0] for b in boots])
        print(f"{pos}: player-seasons={len(r)} TDs={int(n.sum())} pooled share={r.sum()/n.sum():.3f} "
              f"m={m:.3f} [{np.percentile(bm,5):.3f},{np.percentile(bm,95):.3f}] "
              f"k={k:.1f} [{np.percentile(bk,5):.1f},{np.percentile(bk,95):.1f}] (90% player bootstrap)")


# ---------------------------------------------------------------- out-of-sample frame


def season_totals(g: pl.DataFrame) -> pl.DataFrame:
    return g.group_by("player_id", "season").agg(
        pl.col("r").sum(), pl.col("n").sum(), pl.col("carries").sum(), pl.col("targets").sum(),
        pl.col("pos").last())


def build_frame(g: pl.DataFrame, in_season: bool) -> pl.DataFrame:
    """Target rows with lagged history columns.

    in_season=False: one row per player-season (target = whole season, history = prior seasons).
    in_season=True: one row per player-game with a TD (history = prior seasons + this season's
    earlier games, columns r0/n0/c0/t0).
    """
    st = season_totals(g)
    if in_season:
        gg = g.sort("player_id", "season", "season_type", "week")  # REG before POST, weeks in order
        gg = gg.with_columns(
            (pl.col(c).cum_sum().over("player_id", "season") - pl.col(c)).alias(f"{c}0")
            for c in ("r", "n", "carries", "targets"))
        tgt = gg.filter(pl.col("n") > 0).select("player_id", "season", "pos", "r", "n",
                                                pl.col("r0"), pl.col("n0"),
                                                pl.col("carries0").alias("c0"), pl.col("targets0").alias("t0"))
    else:
        tgt = st.filter(pl.col("n") > 0).select("player_id", "season", "pos", "r", "n").with_columns(
            r0=pl.lit(0.0), n0=pl.lit(0.0), c0=pl.lit(0.0), t0=pl.lit(0.0))
    tgt = tgt.filter(pl.col("season").is_in(TARGETS))
    for lag in range(1, MAXLAG + 1):
        lagged = st.select("player_id", (pl.col("season") + lag).alias("season"),
                           pl.col("r").alias(f"r{lag}"), pl.col("n").alias(f"n{lag}"),
                           pl.col("carries").alias(f"c{lag}"), pl.col("targets").alias(f"t{lag}"),
                           pl.col("pos").alias(f"p{lag}"))
        tgt = tgt.join(lagged, on=["player_id", "season"], how="left")
    return tgt.with_columns(pl.col(f"{x}{l}").fill_null(0.0) for x in "rnct" for l in range(1, MAXLAG + 1))


def position_means(g: pl.DataFrame) -> dict[tuple[str, int], float]:
    """Flat position share for target season s from seasons s-5..s-1 (what a yearly build would ship)."""
    out = {}
    for s in TARGETS:
        d = g.filter(pl.col("season").is_between(s - 5, s - 1)).group_by("pos").agg(pl.col("r").sum(), pl.col("n").sum())
        for pos, r, n in d.iter_rows():
            out[(pos, s)] = r / n
    return out


def volume_rates(g: pl.DataFrame) -> dict[tuple[str, int], tuple[float, float]]:
    """Per position and target season: (rush TDs per carry, rec TDs per target) from s-5..s-1."""
    out = {}
    for s in TARGETS:
        d = (g.filter(pl.col("season").is_between(s - 5, s - 1)).group_by("pos")
             .agg(pl.col("rushing_tds").sum() / pl.col("carries").sum(), pl.col("receiving_tds").sum() / pl.col("targets").sum()))
        for pos, a, b in d.iter_rows():
            out[(pos, s)] = (a, b)
    return out


class Arrays:
    def __init__(self, f: pl.DataFrame, pm, vr, pos_filter=None):
        if pos_filter:
            f = f.filter(pl.col("pos") == pos_filter)
        self.f = f
        self.season = f["season"].to_numpy()
        self.pos = f["pos"].to_numpy()
        self.r = f["r"].to_numpy()
        self.n = f["n"].to_numpy()
        self.R = np.stack([f[f"r{l}"].to_numpy() for l in range(MAXLAG + 1)])  # lag 0 = this season to date
        self.N = np.stack([f[f"n{l}"].to_numpy() for l in range(MAXLAG + 1)])
        self.C = np.stack([f[f"c{l}"].to_numpy() for l in range(MAXLAG + 1)])
        self.T = np.stack([f[f"t{l}"].to_numpy() for l in range(MAXLAG + 1)])
        self.m = np.array([pm[(p, s)] for p, s in zip(self.pos, self.season)])
        rates = np.array([vr[(p, s)] for p, s in zip(self.pos, self.season)])
        self.rc, self.rt = rates[:, 0], rates[:, 1]

    def weights(self, window: int, decay: float, current: bool) -> np.ndarray:
        w = np.array([1.0 if current else 0.0] + [decay ** (l - 1) if l <= window else 0.0 for l in range(1, MAXLAG + 1)])
        return w

    def history(self, w):
        return w @ self.R, w @ self.N, w @ self.C, w @ self.T


def logloss(p, r, n):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(r * np.log(p) + (n - r) * np.log(1 - p))


def volume_prior(a: Arrays, C, T, k_vol: float):
    """Prior mean from the player's carries/targets in the window, shrunk to the position mean.

    Expected rush TDs = carries * (pos rush TD/carry), expected rec TDs = targets * (pos rec TD/target);
    their ratio is the volume share, shrunk toward m with k_vol pseudo-TDs of expected TDs.
    """
    er, et = C * a.rc, T * a.rt
    return (er + k_vol * a.m) / (er + et + k_vol)


def score(a: Arrays, mask, window, decay, current, k, prior="pos", k_vol=2.0):
    w = a.weights(window, decay, current)
    R, N, C, T = a.history(w)
    m = a.m if prior == "pos" else volume_prior(a, C, T, k_vol)
    p = (R + k * m) / (N + k)
    return logloss(p[mask], a.r[mask], a.n[mask]).sum() / a.n[mask].sum()


K_GRID = np.array([0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 75, 100, 150, 250, 500, 1000])
WINDOWS = [(0, 1.0, True, "current season to date only"),
           (1, 1.0, False, "last season only"),
           (1, 1.0, True, "to date + last season"),
           (2, 1.0, True, "to date + last 2 seasons"),
           (3, 1.0, True, "to date + last 3 seasons"),
           (MAXLAG, 1.0, True, "to date + all (6) seasons, no decay"),
           (MAXLAG, 0.75, True, "to date + all, decay 0.75/season"),
           (MAXLAG, 0.5, True, "to date + all, decay 0.5/season")]


def best_k(a, mask, window, decay, current, prior="pos", k_vol=2.0):
    losses = np.array([score(a, mask, window, decay, current, k, prior, k_vol) for k in K_GRID])
    return K_GRID[int(np.argmin(losses))], losses


def evaluate(f: pl.DataFrame, pm, vr, label: str) -> dict:
    print(f"\n== Out of sample, {label}: log loss per TD (nats); k tuned on {TUNE[0]}-{TUNE[-1]}, scored on {TEST[0]}-{TEST[-1]} ==")
    chosen = {}
    for pos in SKILL:
        a = Arrays(f, pm, vr, pos)
        tune, test = np.isin(a.season, TUNE), np.isin(a.season, TEST)
        flat = logloss(a.m[test], a.r[test], a.n[test]).sum() / a.n[test].sum()
        print(f"{pos}: test TDs={int(a.n[test].sum())}  flat={flat:.4f}")
        windows = WINDOWS if label.startswith("in-season") else [w for w in WINDOWS if not (w[0] == 0)]
        for window, decay, current, name in windows:
            k, _ = best_k(a, tune, window, decay, current)
            t = score(a, test, window, decay, current, k)
            print(f"    {name:42s} k={k:<6g} test={t:.4f}  gain={flat - t:+.4f} ({100*(flat-t)/flat:+.1f}%)")
        chosen[pos] = a
    return chosen


def bootstrap_k(a: Arrays, window, decay, current, B=200):
    """Bootstrap players: the k minimising total log loss over all target seasons."""
    ids = a.f["player_id"].to_numpy()
    uniq = np.unique(ids)
    w = a.weights(window, decay, current)
    R, N, _, _ = a.history(w)
    # per-row loss for each k, then sum over resampled players
    L = np.stack([logloss((R + k * a.m) / (N + k), a.r, a.n) for k in K_GRID])  # (K, rows)
    per_player = {u: L[:, ids == u].sum(axis=1) for u in uniq}
    P = np.stack([per_player[u] for u in uniq])  # (players, K)
    full = K_GRID[int(np.argmin(P.sum(0)))]
    ks = [K_GRID[int(np.argmin(P[RNG.integers(0, len(uniq), len(uniq))].sum(0)))] for _ in range(B)]
    return full, np.percentile(ks, 5), np.percentile(ks, 95), P.sum(0)


def main() -> None:
    g = load()
    print("player-games:", g.height, "seasons", g["season"].min(), "-", g["season"].max())
    pm, vr = position_means(g), volume_rates(g)
    print("position share (2020-2024 for 2025):", {p: round(pm[(p, 2025)], 3) for p in SKILL})
    print("2-pt rushing share 2015-2025:", g.filter(pl.col("season") >= 2015).group_by("pos").agg(
        (pl.col("rushing_2pt_conversions").sum() / (pl.col("rushing_2pt_conversions") + pl.col("receiving_2pt_conversions")).sum()).round(3),
        (pl.col("rushing_2pt_conversions") + pl.col("receiving_2pt_conversions")).sum().alias("n_2pt")).sort("pos").rows())

    eb_table(g)

    pre = build_frame(g, in_season=False)
    ins = build_frame(g, in_season=True)
    evaluate(pre, pm, vr, "preseason (predict whole season from prior seasons)")
    evaluate(ins, pm, vr, "in-season (predict each game's TDs from everything before it)")

    print("\n== Recommended window (to date + last 3 seasons, no decay), in-season frame, all 2016-2025 targets ==")
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        k, lo, hi, curve = bootstrap_k(a, 3, 1.0, True)
        flat = logloss(a.m, a.r, a.n).sum() / a.n.sum()
        print(f"{pos}: k*={k:g}  90% player-bootstrap [{lo:g}, {hi:g}]  loss/TD at k*={curve.min()/a.n.sum():.4f} flat={flat:.4f}"
              f"  curve(k=2,5,10,20,50)=" + ",".join(f"{curve[list(K_GRID).index(x)]/a.n.sum():.4f}" for x in (2, 5, 10, 20, 50)))

    print("\n== Does the RB/QB prior mean depend on usage? volume prior (carries x TD/carry vs targets x TD/target) ==")
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        tune, test = np.isin(a.season, TUNE), np.isin(a.season, TEST)
        flat = logloss(a.m[test], a.r[test], a.n[test]).sum() / a.n[test].sum()
        k_pos, _ = best_k(a, tune, 3, 1.0, True)
        base = score(a, test, 3, 1.0, True, k_pos)
        best = None
        for kv in (0.5, 1, 2, 4, 8):
            k, _ = best_k(a, tune, 3, 1.0, True, prior="vol", k_vol=kv)
            tl = score(a, tune, 3, 1.0, True, k, "vol", kv)
            if best is None or tl < best[0]:
                best = (tl, kv, k)
        _, kv, k = best
        t = score(a, test, 3, 1.0, True, k, "vol", kv)
        # volume prior alone (k -> large)
        t_vol_only = score(a, test, 3, 1.0, True, 1e6, "vol", kv)
        print(f"{pos}: flat={flat:.4f}  EB->pos (k={k_pos:g}) {base:.4f}  EB->volume (k_vol={kv:g}, k={k:g}) {t:.4f}"
              f"  volume prior alone {t_vol_only:.4f}")

    # RB carry-share tiers of the realised share vs the EB-shrunk predictions (calibration)
    print("\n== Calibration, RB in-season, to date + 3 seasons, k*: predicted vs realised rushing share by predicted bin ==")
    for pos in ("RB",):
        a = Arrays(ins, pm, vr, pos)
        k, *_ = bootstrap_k(a, 3, 1.0, True, B=1)
        R, N, _, _ = a.history(a.weights(3, 1.0, True))
        p = (R + k * a.m) / (N + k)
        for lo, hi in ((0, .6), (.6, .7), (.7, .8), (.8, .85), (.85, .9), (.9, 1.01)):
            sel = (p >= lo) & (p < hi)
            if sel.any():
                print(f"  pred [{lo:.2f},{hi:.2f}): TDs={int(a.n[sel].sum()):5d} mean pred={np.average(p[sel], weights=a.n[sel]):.3f} realised={a.r[sel].sum()/a.n[sel].sum():.3f}")

    print("\n== Position changers: history from seasons at another position ==")
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        f = a.f
        other = np.stack([(f[f"p{l}"].fill_null(pos).to_numpy() != pos) & (f[f"n{l}"].to_numpy() > 0) for l in range(1, MAXLAG + 1)])
        rows = other[:3].any(0)
        print(f"{pos}: target rows with TD history at another position (last 3 seasons): {rows.sum()} rows, "
              f"{int(a.n[rows].sum())} TDs ({100*a.n[rows].sum()/a.n.sum():.2f}% of TDs)")
        if rows.any():
            k, *_ = bootstrap_k(a, 3, 1.0, True, B=1)
            w = a.weights(3, 1.0, True)
            R, N, _, _ = a.history(w)
            keep = logloss((R + k * a.m) / (N + k), a.r, a.n)[rows].sum()
            Rd, Nd = R.copy(), N.copy()
            for l in range(1, 4):
                Rd -= np.where(other[l - 1], a.R[l], 0)
                Nd -= np.where(other[l - 1], a.N[l], 0)
            drop = logloss((Rd + k * a.m) / (Nd + k), a.r, a.n)[rows].sum()
            print(f"    loss on those TDs: keep history {keep/a.n[rows].sum():.4f}  drop other-position seasons {drop/a.n[rows].sum():.4f}"
                  f"  flat {logloss(a.m, a.r, a.n)[rows].sum()/a.n[rows].sum():.4f}")

    print("\n== No history ==")
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        R, N, _, _ = a.history(a.weights(3, 1.0, True))
        print(f"{pos}: share of target TDs whose scorer had 0 TDs in the window: {100*a.n[N == 0].sum()/a.n.sum():.1f}%")



# ---------------------------------------------------------------- follow-up: volume prior grid


KV_GRID = [0.25, 0.5, 1, 2, 4, 8, 16]
K2_GRID = [2, 5, 10, 20, 30, 50, 100, 200, 1e6]


def volume_grid() -> None:
    g = load()
    pm, vr = position_means(g), volume_rates(g)
    ins = build_frame(g, in_season=True)
    print("\n== Volume prior: windows x (k_vol, k), in-season frame; tuned 2016-2020, test 2021-2025 ==")
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        tune, test = np.isin(a.season, TUNE), np.isin(a.season, TEST)
        flat = logloss(a.m[test], a.r[test], a.n[test]).sum() / a.n[test].sum()
        print(f"{pos}: flat test={flat:.4f}")
        for window, decay, current, name in WINDOWS[1:] + [(2, 1.0, False, "last 2 seasons only (no current)"),
                                                            (3, 1.0, False, "last 3 seasons only (no current)")]:
            best = min(((score(a, tune, window, decay, current, k, "vol", kv), kv, k) for kv in KV_GRID for k in K2_GRID))
            _, kv, k = best
            t = score(a, test, window, decay, current, k, "vol", kv)
            all_ = score(a, np.ones_like(tune), window, decay, current, k, "vol", kv)
            print(f"    {name:42s} k_vol={kv:<5g} k={k:<8g} test={t:.4f} gain={flat-t:+.4f} ({100*(flat-t)/flat:+.1f}%)  all-years={all_:.4f}")

    print("\n== Fixed recommendation: to date + last 2 seasons, k_vol=1, k=30 for every position ==")
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        test = np.isin(a.season, TEST)
        flat = logloss(a.m[test], a.r[test], a.n[test]).sum() / a.n[test].sum()
        eb_pos_k, _ = best_k(a, np.isin(a.season, TUNE), 2, 1.0, True)
        ebp = score(a, test, 2, 1.0, True, eb_pos_k)
        rows = []
        for kv, k in ((1, 30), (1, 20), (1, 50), (0.5, 30), (2, 30), (1, 1e6)):
            rows.append(f"(kv={kv},k={k:g}) {score(a, test, 2, 1.0, True, k, 'vol', kv):.4f}")
        print(f"{pos}: flat {flat:.4f} | EB->pos k={eb_pos_k:g} {ebp:.4f} | " + "  ".join(rows))
        # without the current season (a build-time file not refreshed in season)
        print(f"      no current season: (kv=1,k=30) {score(a, test, 2, 1.0, False, 30, 'vol', 1):.4f}")

    print("\n== Bootstrap (players) of k with k_vol=1, to date + last 2 seasons, all target years ==")
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        ids = a.f["player_id"].to_numpy()
        uniq, inv = np.unique(ids, return_inverse=True)
        R, N, C, T = a.history(a.weights(2, 1.0, True))
        v = volume_prior(a, C, T, 1.0)
        kg = np.array([1, 2, 3, 5, 7, 10, 15, 20, 30, 40, 50, 75, 100, 150, 200, 300, 500, 1e6])
        L = np.stack([np.bincount(inv, logloss((R + k * v) / (N + k), a.r, a.n), len(uniq)) for k in kg])  # (K, players)
        kstar = kg[np.argmin(L.sum(1))]
        ks = [kg[np.argmin(L[:, RNG.integers(0, len(uniq), len(uniq))].sum(1))] for _ in range(300)]
        curve = L.sum(1) / a.n.sum()
        print(f"{pos}: k*={kstar:g} 90% [{np.percentile(ks,5):g}, {np.percentile(ks,95):g}]  loss/TD: " +
              " ".join(f"k={k:g}:{c:.4f}" for k, c in zip(kg, curve) if k in (5, 10, 20, 30, 50, 100, 1e6)))

    print("\n== TE without Taysom Hill (00-0033357) ==")
    a = Arrays(ins.filter(pl.col("player_id") != "00-0033357"), pm, vr, "TE")
    test = np.isin(a.season, TEST)
    flat = logloss(a.m[test], a.r[test], a.n[test]).sum() / a.n[test].sum()
    print(f"flat {flat:.4f}  EB->pos k=10 {score(a, test, 2, 1.0, True, 10):.4f}  k=30 {score(a, test, 2, 1.0, True, 30):.4f}"
          f"  vol(kv=1,k=30) {score(a, test, 2, 1.0, True, 30, 'vol', 1):.4f}")
    th = ins.filter(pl.col("player_id") == "00-0033357")
    print("Taysom Hill TDs in TE target rows:", th["n"].sum(), "of", ins.filter(pl.col("pos") == "TE")["n"].sum())

    print("\n== Volume rates per position (2020-2024, used for 2025): rush TD/carry, rec TD/target ==")
    print({p: tuple(round(x, 4) for x in vr[(p, 2025)]) for p in SKILL})


# ---------------------------------------------------------------- final recommendation

KAPPA, K_FINAL, WINDOW = 2.0, 30.0, 2   # pseudo expected-TDs toward m, pseudo-TDs toward v, prior seasons


def final() -> None:
    g = load()
    pm, vr = position_means(g), volume_rates(g)
    ins = build_frame(g, in_season=True)
    print(f"\n== Recommended: to date + last {WINDOW} seasons, kappa={KAPPA:g}, k={K_FINAL:g} (log loss per TD, nats) ==")
    tot = {"flat": 0.0, "ebpos": 0.0, "final": 0.0, "n": 0.0}
    for pos in SKILL:
        a = Arrays(ins, pm, vr, pos)
        test = np.isin(a.season, TEST)
        kpos, _ = best_k(a, np.isin(a.season, TUNE), WINDOW, 1.0, True)
        R, N, C, T = a.history(a.weights(WINDOW, 1.0, True))
        v = volume_prior(a, C, T, KAPPA)
        p = (R + K_FINAL * v) / (N + K_FINAL)
        pe = (R + kpos * a.m) / (N + kpos)
        for name, q in (("flat", a.m), ("ebpos", pe), ("final", p)):
            tot[name] += logloss(q[test], a.r[test], a.n[test]).sum()
        tot["n"] += a.n[test].sum()
        f_, e_, x_ = (logloss(q[test], a.r[test], a.n[test]).sum() / a.n[test].sum() for q in (a.m, pe, p))
        print(f"{pos}: test TDs={int(a.n[test].sum())} flat={f_:.4f}  EB->position (k={kpos:g})={e_:.4f} ({f_-e_:+.4f})"
              f"  EB->volume={x_:.4f} ({f_-x_:+.4f}, {100*(f_-x_)/f_:+.1f}%)")
        if pos in ("RB", "WR"):
            edges = (0, .6, .7, .8, .85, .9, 1.01) if pos == "RB" else (0, .02, .04, .08, .2, 1.01)
            for lo, hi in zip(edges[:-1], edges[1:]):
                sel = (p >= lo) & (p < hi)
                if sel.any():
                    print(f"     calib pred [{lo:.2f},{hi:.2f}): TDs={int(a.n[sel].sum()):5d} pred={np.average(p[sel], weights=a.n[sel]):.3f} realised={a.r[sel].sum()/a.n[sel].sum():.3f}")
    print(f"ALL: flat={tot['flat']/tot['n']:.4f} EB->position={tot['ebpos']/tot['n']:.4f} EB->volume={tot['final']/tot['n']:.4f}")

    # examples: share going into 2026 week 11 (2024-2025 + 2026 weeks 1-10)
    nfl.config.update_config(cache_mode="filesystem")
    ps = nfl.load_player_stats([2024, 2025, 2026])
    h = (ps.with_columns(pl.col("rushing_tds", "receiving_tds", "carries", "targets").fill_null(0))
         .group_by("player_id").agg(pl.col("player_display_name").last(), pl.col("position").last(),
                                    pl.col("rushing_tds").sum().alias("R"), (pl.col("rushing_tds") + pl.col("receiving_tds")).sum().alias("N"),
                                    pl.col("carries").sum().alias("C"), pl.col("targets").sum().alias("T")))
    names = ["Christian McCaffrey", "Derrick Henry", "Alvin Kamara", "Bijan Robinson", "Jahmyr Gibbs", "De'Von Achane",
             "Saquon Barkley", "Josh Jacobs", "Lamar Jackson", "Jalen Hurts", "Josh Allen", "Deebo Samuel",
             "Ja'Marr Chase", "Taysom Hill", "Travis Kelce"]
    print("\n== Examples going into 2026 week 11 (2024 + 2025 + 2026 wk1-10, all game types) ==")
    for row in h.filter(pl.col("player_display_name").is_in(names)).sort("position", "player_display_name").iter_rows(named=True):
        pos = row["position"]
        m = pm[(pos, 2025)]  # stand-in for the 2021-2025 build
        rc, rt = vr[(pos, 2025)]
        v = (rc * row["C"] + KAPPA * m) / (rc * row["C"] + rt * row["T"] + KAPPA)
        sh = (row["R"] + K_FINAL * v) / (row["N"] + K_FINAL)
        raw = row["R"] / row["N"] if row["N"] else float("nan")
        print(f"  {row['player_display_name']:20s} {pos} R/N={int(row['R'])}/{int(row['N'])} ({raw:.2f}) C={int(row['C'])} T={int(row['T'])}"
              f"  m={m:.3f} v={v:.3f} share={sh:.3f}")


if __name__ == "__main__":
    import sys
    if "--volume" in sys.argv:
        volume_grid()
    elif "--final" in sys.argv:
        final()
    else:
        main()
