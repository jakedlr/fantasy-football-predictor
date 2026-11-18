#!/usr/bin/env python
"""Backtest the kicker and D/ST models (PLAN S6.6) against nflverse outcomes and Kalshi's lines.

    python scripts/backtest_k_dst.py [--seasons 2016-2025] [--samples 2000] [--out data/backtest-k-dst]
                                     [--kalshi data/kalshi] [--no-kalshi] [--jobs N]

1. Walk-forward on nflverse: each test season's models are fitted on earlier seasons only (the
   kicker on the 5 before it, as scripts/build_params.py fits it; the D/ST on every season from
   2015), and every team-game of the season is scored from its closing lines. Baselines: the
   last 2 seasons' kicker points; every earlier D/ST game, equally weighted.
2. Kalshi (2026): each KXNFLFFPTS kicker and D/ST line against the model's P(points > line) from
   nflverse's closing lines and the committed params, and against the outcome. Lines with a
   spread over 10¢ are kept as their own group.

Scored under the ESPN league's rules and Sleeper's (Kalshi settles on Sleeper). Writes summary.json
and tables.md under --out. Needs nflreadpy (network once, then its cache).
"""

from __future__ import annotations

import argparse
import json
import sys
import zlib
from collections import defaultdict
from pathlib import Path

import numpy as np
import polars as pl

import build_params as bp
from ffp import metrics
from ffp.dst import AnalogTable, analog_table, analog_weights
from ffp.kicker import sample_kicker
from ffp.scoring import load_rules, score_dst, score_kicker

ROOT = Path(__file__).resolve().parents[1]
RULES = {"espn": load_rules(ROOT / "src" / "ffp" / "rules" / "espn-league.json"), "sleeper": load_rules("sleeper")}
KICKER_WINDOW = 5
BASELINE_SEASONS = 2
PCT = (5, 25, 75, 95)


def _seed(*parts) -> int:
    return zlib.crc32("|".join(map(str, parts)).encode())


def _row(samples: np.ndarray, y: float, rng) -> dict:
    q = np.quantile(samples, [p / 100 for p in PCT], method="inverted_cdf")
    return {"y": float(y), "mean": float(samples.mean()), "pit": metrics.pit_from_samples(samples, y, rng),
            "crps": metrics.crps_from_samples(samples, y), **{f"p{p}": float(v) for p, v in zip(PCT, q)}}


def summarize(rows: list[dict]) -> dict:
    """n, CRPS, PIT uniformity, P5–P95 and P25–P75 coverage, bias (y − mean)."""
    if not rows:
        return {"n": 0}
    y = np.array([r["y"] for r in rows])
    pit = metrics.pit_histogram([r["pit"] for r in rows])
    return {"n": len(rows), "crps": float(np.mean([r["crps"] for r in rows])), "pit_p": pit.chi2_p,
            "pit_counts": pit.counts.tolist(),
            "cov90": metrics.coverage([r["p5"] for r in rows], [r["p95"] for r in rows], y),
            "cov50": metrics.coverage([r["p25"] for r in rows], [r["p75"] for r in rows], y),
            "bias": float(np.mean(y - np.array([r["mean"] for r in rows])))}


# --- kickers ---------------------------------------------------------------------------


def kicker_line(row: dict) -> dict:
    """An nflverse team-game's kicking as score_kicker's stat line."""
    return {"pat_made": row["pat_made"], "pat_missed": row["pat_att"] - row["pat_made"], "fg_missed": len(row["missed"]),
            "fg_made_yards": np.array(row["made"], dtype=float) if row["made"] else np.empty(0)}


def kicker_walk_forward(ps: pl.DataFrame, schedules: pl.DataFrame, seasons: list[int], n: int, jobs: int = 1) -> dict:
    return _by_season("kicker", seasons, n, jobs, ps=ps, schedules=schedules)


def _kicker_season(t: int, n: int, ps: pl.DataFrame, schedules: pl.DataFrame) -> dict:
    out = defaultdict(list)
    p = bp.kicker_params(ps, schedules, list(range(t - KICKER_WINDOW, t)))
    games = bp.kicking_games(ps.filter(pl.col("season") == t), bp.team_game_lines(schedules, [t])).to_dicts()
    past = bp.kicking_games(ps.filter(pl.col("season").is_between(t - BASELINE_SEASONS, t - 1)),
                            bp.team_game_lines(schedules, list(range(t - BASELINE_SEASONS, t)))).to_dicts()
    baseline = {name: np.array([score_kicker(kicker_line(g), rules) for g in past]) for name, rules in RULES.items()}
    rng = np.random.default_rng(t)
    for g in games:
        k = sample_kicker(g["itt"], g["fav_by"], t, n=n, seed=_seed("K", g["game_id"], g["team"]), p=p)
        for name, rules in RULES.items():
            y = score_kicker(kicker_line(g), rules)
            model = np.asarray(score_kicker(k.stat_line(), rules), dtype=float)
            out[f"model {name} {t}"].append(_row(model, y, rng))
            out[f"baseline {name} {t}"].append(_row(baseline[name], y, rng))
    return out


# --- D/ST ------------------------------------------------------------------------------


def _table(d: pl.DataFrame) -> AnalogTable:
    return AnalogTable({c: d[c].to_numpy() if d[c].dtype == pl.String else d[c].to_numpy().astype(float)
                        for c in d.columns})


def dst_walk_forward(d: pl.DataFrame, seasons: list[int], n: int, jobs: int = 1) -> dict:
    return _by_season("dst", seasons, n, jobs, d=d)


def _dst_season(t: int, n: int, d: pl.DataFrame) -> dict:
    out = defaultdict(list)
    hist, test = _table(d.filter(pl.col("season") < t)), d.filter(pl.col("season") == t).to_dicts()
    rng = np.random.default_rng(t)
    flat = np.full(len(hist), 1 / len(hist))
    for g in test:
        w = analog_weights(g["opp_itt"], t, hist)
        draw = np.random.default_rng(_seed("DST", g["season"], g["week"], g["team"]))
        rows = hist.rows(draw.choice(len(hist), size=n, p=w))
        base = hist.rows(draw.choice(len(hist), size=n, p=flat))
        for name, rules in RULES.items():
            y = score_dst(g, rules)
            out[f"model {name} {t}"].append(_row(np.asarray(score_dst(rows, rules), dtype=float), y, rng))
            out[f"baseline {name} {t}"].append(_row(np.asarray(score_dst(base, rules), dtype=float), y, rng))
    return out


# --- seasons in worker processes (S8.3) -------------------------------------------------

_SEASON = {"kicker": _kicker_season, "dst": _dst_season}
_DATA: dict = {}


def _start_worker(data: dict) -> None:
    _DATA.update(data)


def _season_in_worker(task):
    model, t, n = task
    return _SEASON[model](t, n, **_DATA)


def _by_season(model: str, seasons: list[int], n: int, jobs: int, **data) -> dict:
    """Each test season's rows, merged in season order. Every season is seeded on its own, so the rows
    don't depend on `jobs`: with more than one, seasons run in that many worker processes, each sent
    the data once."""
    if jobs <= 1 or len(seasons) <= 1:
        parts = [_SEASON[model](t, n, **data) for t in seasons]
    else:
        from ffp.workers import in_order, process_pool

        # Workers import this script by name: put its directory where they look (spawned workers start
        # from this process's sys.path).
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        with process_pool(min(jobs, len(seasons)), _start_worker, (data,)) as pool:
            parts = list(in_order(pool, _season_in_worker, [(model, t, n) for t in seasons]))
    out = defaultdict(list)
    for part in parts:
        for key, rows in part.items():
            out[key] += rows
    return out


# --- Kalshi ----------------------------------------------------------------------------


def kalshi_comparison(root: Path, ps: pl.DataFrame, schedules: pl.DataFrame, d2026: pl.DataFrame, n: int) -> dict:
    """Model P(points > line) against Kalshi's mid, both scored against the outcome (Sleeper)."""
    from ffp.sources.kalshi import load_fantasy_lines

    rules = RULES["sleeper"]
    tl = bp.team_game_lines(schedules, [2026])
    by_team = {(r["game_id"], r["team"]): r for r in tl.to_dicts()}
    kickers = {(r["game_id"], r["player_id"]): r for r in
               ps.filter(pl.col("season") == 2026, pl.col("position") == "K").to_dicts()}
    dst = {(r["season"], r["week"], r["team"]): r for r in d2026.to_dicts()}
    week_of = {r["game_id"]: (r["season"], r["week"]) for r in tl.to_dicts()}
    table = analog_table()
    rows = defaultdict(list)
    for line in load_fantasy_lines(root, keep_wide=True):
        if line.gsis_id.startswith("DST-"):
            team = line.gsis_id.removeprefix("DST-")
            g, actual = by_team.get((line.game_id, team)), dst.get((*week_of.get(line.game_id, (0, 0)), team))
            if g is None or actual is None:
                continue
            w = analog_weights(g["opp_itt"], 2026, table)
            draw = np.random.default_rng(_seed("KDST", line.game_id, team))
            samples = np.asarray(score_dst(table.rows(draw.choice(len(table), size=n, p=w)), rules), dtype=float)
            y, kind = score_dst(actual, rules), "D/ST"
        else:
            k_row = kickers.get((line.game_id, line.gsis_id))
            g = by_team.get((line.game_id, k_row["team"])) if k_row else None
            if g is None:
                continue
            made = [int(x) for x in (k_row["fg_made_list"] or "").split(";") if x]
            missed = len([x for x in ((k_row["fg_missed_list"] or "") + ";" + (k_row["fg_blocked_list"] or "")).split(";") if x])
            actual = {"pat_made": k_row["pat_made"] or 0, "pat_missed": (k_row["pat_att"] or 0) - (k_row["pat_made"] or 0),
                      "fg_missed": missed, "fg_made_yards": np.array(made, dtype=float) if made else np.empty(0)}
            k = sample_kicker(g["itt"], g["fav_by"], 2026, n=n, seed=_seed("KK", line.game_id, line.gsis_id))
            samples = np.asarray(score_kicker(k.stat_line(), rules), dtype=float)
            y, kind = score_kicker(actual, rules), "K"
        group = f"{kind} {'wide' if line.wide else 'priced'}"
        rows[group].append({"model": float(np.mean(samples > line.line)), "mid": line.mid, "won": float(y > line.line),
                            "line": line.line, "median": float(np.median(samples))})
    out = {}
    for group, rs in sorted(rows.items()):
        model, mid, won = (np.array([r[k] for r in rs]) for k in ("model", "mid", "won"))
        out[group] = {"n": len(rs), "observed": float(won.mean()), "model_mean_p": float(model.mean()),
                      "mid_mean_p": float(mid.mean()),
                      "log_loss_model": metrics.log_loss(model, won, eps=1 / (2 * n)),
                      "log_loss_mid": metrics.log_loss(mid, won), "log_loss_coin": float(np.log(2)),
                      "brier_model": float(np.mean((model - won) ** 2)), "brier_mid": float(np.mean((mid - won) ** 2)),
                      "corr_model_mid": float(np.corrcoef(model, mid)[0, 1]) if len(rs) > 2 else None,
                      "corr_median_line": float(np.corrcoef([r["median"] for r in rs], [r["line"] for r in rs])[0, 1])
                      if len(rs) > 2 else None}
    return out


# --- report ----------------------------------------------------------------------------


def pooled(raw: dict, seasons: list[int]) -> dict:
    """Per (model or baseline, rules): each season and all seasons."""
    out = {}
    for kind in ("model", "baseline"):
        for name in RULES:
            out[f"{kind} {name} all"] = summarize([r for t in seasons for r in raw[f"{kind} {name} {t}"]])
            for t in seasons:
                out[f"{kind} {name} {t}"] = summarize(raw[f"{kind} {name} {t}"])
    return out


def _f(x, d=3):
    return "–" if x is None else f"{x:.{d}f}"


def render(summary: dict) -> str:
    out = []
    for position in ("kicker", "dst"):
        out += [f"### {position.upper() if position == 'dst' else 'Kicker'}: walk-forward on nflverse", "",
                "| Model | Rules | Season | n | CRPS | PIT χ² p | P5–P95 | P25–P75 | bias (y − mean) |",
                "|---|---|---|---|---|---|---|---|---|"]
        for key, s in summary[position].items():
            kind, rules, season = key.split()
            if s["n"]:
                out.append(f"| {kind} | {rules} | {season} | {s['n']} | {_f(s['crps'])} | {_f(s['pit_p'])} | "
                           f"{100 * s['cov90']:.1f}% | {100 * s['cov50']:.1f}% | {_f(s['bias'], 2)} |")
        out.append("")
    if summary.get("kalshi"):
        out += ["### Kalshi KXNFLFFPTS lines, 2026 (Sleeper scoring)", "",
                "| Group | n | observed P(over) | mean p model / mid | log loss model / mid / coin | Brier model / mid | corr(model, mid) |",
                "|---|---|---|---|---|---|---|"]
        for group, s in summary["kalshi"].items():
            out.append(f"| {group} | {s['n']} | {_f(s['observed'])} | {_f(s['model_mean_p'])} / {_f(s['mid_mean_p'])} | "
                       f"{_f(s['log_loss_model'])} / {_f(s['log_loss_mid'])} / {_f(s['log_loss_coin'])} | "
                       f"{_f(s['brier_model'], 4)} / {_f(s['brier_mid'], 4)} | {_f(s['corr_model_mid'], 2)} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    import nflreadpy as nfl

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seasons", default="2016-2025", help="test seasons (default 2016-2025)")
    ap.add_argument("--samples", type=int, default=2000, help="Monte Carlo samples per team-game")
    ap.add_argument("--out", type=Path, default=Path("data") / "backtest-k-dst")
    ap.add_argument("--kalshi", type=Path, default=Path("data") / "kalshi")
    ap.add_argument("--no-kalshi", action="store_true")
    ap.add_argument("--jobs", type=int, default=1, help="worker processes for the test seasons (default 1)")
    args = ap.parse_args(argv)
    seasons = bp.parse_seasons(args.seasons)

    nfl.config.update_config(cache_mode="filesystem")
    first = min(seasons) - KICKER_WINDOW
    all_seasons = list(range(first, 2027))
    ps, schedules = nfl.load_player_stats(all_seasons), nfl.load_schedules(all_seasons)
    plays = pl.concat([nfl.load_pbp([s]).select(bp.DST_PLAY_COLUMNS) for s in range(2015, 2027)], how="vertical_relaxed")
    d = bp.dst_games(plays, schedules, list(range(2015, 2027)))

    summary = {"kicker": pooled(kicker_walk_forward(ps, schedules, seasons, args.samples, args.jobs), seasons),
               "dst": pooled(dst_walk_forward(d, seasons, args.samples, args.jobs), seasons)}
    if not args.no_kalshi:
        summary["kalshi"] = kalshi_comparison(args.kalshi, ps, schedules, d.filter(pl.col("season") == 2026),
                                              args.samples)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    (args.out / "tables.md").write_text(render(summary))
    print(render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
