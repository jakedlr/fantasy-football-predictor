"""The Kalshi Backtest (PLAN S5.2): the production pipeline on Kalshi's Ladders, scored against nflverse.

Per (Game, player) with Kalshi Rungs:

1. Inputs as of kickoff, no look-ahead: the player's position from the nflverse weekly roster
   of that week (or the latest before it), his rushing TD share from history strictly before
   the Game (`td_share.load_td_history(before=...)`), his trailing carry share.
2. The same fit and simulation as `ffp predict`: `to_prop_markets` -> `predict.fit_player`
   with Kalshi's mids as the Fair Probabilities -> `predict.score_player` under the `sleeper`
   preset, with the position's copula and with independent stats (the baseline), same seed.
3. One JSON record per player-game (`evaluate`), cached as JSONL so a run resumes (`run`).
   A player with no nflverse stat row but offensive snaps played without a stat: an all-zero
   outcome. One with neither didn't play: his record says "dnp" and nothing is scored.
4. `summarize` pools the records into the report's tables; `render_tables` writes them as
   Markdown.

Parameters are not changed here: the diagnostics only measure (PLAN: findings change nothing
automatically).
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import time
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence, Set
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.special import logit, ndtri

from ffp import metrics, params
from ffp.distributions import (
    StatDistribution,
    fit_yards,
    passing_yards_sd_relation,
    prob_negative,
    prob_zero,
    receiving_yards_sd_relation,
    rushing_yards_sd_relation,
)
from ffp.fair import KALSHI_PRIOR_C, PROB_FLOOR
from ffp.outcomes import OUTCOME_STATS, fantasy_points
from ffp.predict import (
    MODELED_POSITIONS,
    NO_CORRELATION,
    TD_STAT,
    Fitted,
    fit_player,
    has_scoring_markets,
    score_player,
    stat_distributions,
)
from ffp.scoring import PRESETS, ScoringRules
from ffp.simulate import sample_joint
from ffp.sources.kalshi import KalshiFantasyLine, KalshiFantasyMean, KalshiRung, to_prop_markets
from ffp.td_share import TdHistory, rushing_td_share

log = logging.getLogger(__name__)

RULES: ScoringRules = PRESETS["sleeper"]  # what KXNFLFFPTS and KXNFLFFPTSLADDER settle on
PRIOR_C = {"kalshi": KALSHI_PRIOR_C}  # an exchange's mid is its Fair Probability
DEFAULT_SAMPLES = 4000
DEFAULT_SEED = 20261116
PERCENTILES = (5, 10, 25, 50, 75, 90, 95)
YARDS_STATS = ("receiving_yards", "rushing_yards", "passing_yards")
# The free-fit sd (no prior) is compared with the relation only with this many Rungs.
FREE_SD_MIN_RUNGS = 4
# Trailing carry share: the mean over the player's previous up-to-4 games of the season, ≥ 2 games.
TRAILING_GAMES, TRAILING_MIN_GAMES = 4, 2
WORKHORSE_SHARE = 0.55  # research/nflverse-data.md 3d

# Record statuses: "ok" is scored; the rest are counted and left out.
OK, DNP, NO_POSITION, UNMODELED, NO_MARKETS, UNFITTABLE = (
    "ok", "dnp", "no_position", "unmodeled_position", "no_markets", "unfittable")

# The outcome of each evaluated stat, from an outcomes.load_outcomes value.
_OUTCOME = {TD_STAT: lambda o: o["rushing_tds"] + o["receiving_tds"]}


def _outcome(stat: str, outcome: Mapping[str, float]) -> int:
    return int(round(_OUTCOME[stat](outcome) if stat in _OUTCOME else outcome[stat]))


# --- inputs ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerGame:
    """One player's Kalshi Rungs in one Game, every stat."""

    game_id: str
    season: int
    week: int
    kickoff: str
    gsis_id: str
    player: str
    rungs: tuple[KalshiRung, ...]

    @property
    def key(self) -> str:
        return f"{self.game_id}|{self.gsis_id}"


def player_games(rungs: Iterable[KalshiRung]) -> list[PlayerGame]:
    """The Rungs grouped by (Game, player), in kickoff order."""
    groups: dict[tuple[str, str], list[KalshiRung]] = {}
    for r in rungs:
        groups.setdefault((r.game_id, r.gsis_id), []).append(r)
    out = [PlayerGame(rs[0].game_id, rs[0].season, rs[0].week, rs[0].kickoff, rs[0].gsis_id, rs[0].player,
                      tuple(rs)) for rs in groups.values()]
    return sorted(out, key=lambda pg: (pg.kickoff, pg.game_id, pg.gsis_id))


class Positions:
    """Each player's nflverse roster position as of a week: that week's row, else the latest before it."""

    def __init__(self, rows: Iterable[Mapping]):
        by_player: dict[str, dict[tuple[int, int], str]] = defaultdict(dict)
        for row in rows:
            pid, position = row.get("gsis_id"), row.get("position")
            if not pid or not position:
                continue
            by_player[str(pid)][int(row["season"]), int(row["week"])] = str(position)
        self._weeks = {pid: sorted(weeks) for pid, weeks in by_player.items()}
        self._positions = {pid: [weeks[w] for w in self._weeks[pid]] for pid, weeks in by_player.items()}

    def at(self, gsis_id: str, season: int, week: int) -> str | None:
        weeks = self._weeks.get(gsis_id)
        if not weeks:
            return None
        i = bisect.bisect_right(weeks, (season, week)) - 1
        return None if i < 0 else self._positions[gsis_id][i]


def trailing_carry_shares(rows: Iterable[Mapping]) -> dict[tuple[str, str], float]:
    """{(game_id, gsis_id): the player's mean share of his team's carries over his previous
    TRAILING_GAMES games of the season} (research/nflverse-data.md 3d), from nflverse weekly
    player-stats rows; only player-games with at least TRAILING_MIN_GAMES earlier games."""
    rows = [r for r in rows if r.get("player_id") and r.get("game_id")]
    team_carries: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        team_carries[str(r["game_id"]), str(r.get("team"))] += _num(r.get("carries"))
    games: dict[tuple[str, int], list[tuple[int, str, float]]] = defaultdict(list)
    for r in rows:
        total = team_carries[str(r["game_id"]), str(r.get("team"))]
        share = _num(r.get("carries")) / total if total else 0.0
        games[str(r["player_id"]), int(r["season"])].append((int(r["week"]), str(r["game_id"]), share))
    out = {}
    for (pid, _), played in games.items():
        played.sort()
        for i, (_, game_id, _) in enumerate(played):
            before = played[max(0, i - TRAILING_GAMES):i]
            if len(before) >= TRAILING_MIN_GAMES:
                out[game_id, pid] = float(np.mean([s for _, _, s in before]))
    return out


def _num(value) -> float:
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return 0.0
    return float(value)


@dataclass(frozen=True)
class Task:
    """Everything `evaluate` needs for one player-game, known before kickoff, plus his outcome."""

    game: PlayerGame
    position: str
    share: float  # rushing TD share from history strictly before the Game
    outcome: dict[str, float] | None  # None: no nflverse row, he didn't play
    carry_share: float | None = None
    line: tuple[float, float] | None = None  # KXNFLFFPTS (line, mid)
    market_mean: float | None = None  # KXNFLFFPTSLADDER expected points
    no_stat_row: bool = False  # played (offensive snaps) but has no stat row: an all-zero outcome


@dataclass
class Context:
    """The nflverse and Kalshi data the Tasks are built from."""

    positions: Positions
    outcomes: Mapping[tuple[str, str], dict[str, float]]
    td_history: Callable[[int, int], Mapping[str, TdHistory]]  # (season, week) -> history before it
    carry_shares: Mapping[tuple[str, str], float] = field(default_factory=dict)
    lines: Mapping[tuple[str, str], KalshiFantasyLine] = field(default_factory=dict)
    means: Mapping[tuple[str, str], KalshiFantasyMean] = field(default_factory=dict)
    played: Set[tuple[str, str]] = field(default_factory=set)  # (game_id, gsis_id) with offensive snaps


def played_games(snaps: Iterable[Mapping], roster: Iterable[Mapping]) -> set[tuple[str, str]]:
    """(game_id, gsis_id) of every player with an offensive snap, from nflverse snap counts
    (keyed by PFR id) and roster rows mapping `pfr_id` to `gsis_id`."""
    gsis = {str(r["pfr_id"]): str(r["gsis_id"]) for r in roster if r.get("pfr_id") and r.get("gsis_id")}
    return {(str(s["game_id"]), gsis[str(s["pfr_player_id"])]) for s in snaps
            if _num(s.get("offense_snaps")) > 0 and str(s.get("pfr_player_id")) in gsis}


def tasks(games: Iterable[PlayerGame], ctx: Context) -> tuple[list[Task], list[dict]]:
    """(the player-games to evaluate, records of those skipped before fitting: no roster
    position, or a position the model doesn't cover)."""
    out, skipped = [], []
    for pg in games:
        position = ctx.positions.at(pg.gsis_id, pg.season, pg.week)
        if position is None or position not in MODELED_POSITIONS:
            skipped.append(_record(pg, position, NO_POSITION if position is None else UNMODELED))
            continue
        key = (pg.game_id, pg.gsis_id)
        share = rushing_td_share(position, ctx.td_history(pg.season, pg.week).get(pg.gsis_id))
        line, mean = ctx.lines.get(key), ctx.means.get(key)
        outcome, no_row = ctx.outcomes.get(key), False
        if outcome is None and key in ctx.played:
            outcome, no_row = {stat: 0.0 for stat in OUTCOME_STATS}, True
        out.append(Task(pg, position, share, outcome, ctx.carry_shares.get(key),
                        None if line is None else (line.line, line.mid),
                        None if mean is None else mean.expected_points, no_row))
    return out, skipped


def _record(pg: PlayerGame, position: str | None, status: str, **extra) -> dict:
    return {"key": pg.key, "game_id": pg.game_id, "season": pg.season, "week": pg.week,
            "gsis_id": pg.gsis_id, "player": pg.player, "position": position, "status": status, **extra}


# --- one player-game --------------------------------------------------------------------


def fingerprint(task: Task, samples: int, seed: int, diagnostics: bool) -> str:
    """A hash of everything a record is computed from: a cached record with another is stale."""
    inputs = [[asdict(r) for r in task.game.rungs], task.position, task.share, task.outcome, task.carry_share,
              task.line, task.market_mean, samples, seed, diagnostics]
    return f"{zlib.crc32(json.dumps(inputs, sort_keys=True).encode()):08x}"


def evaluate(task: Task, samples: int = DEFAULT_SAMPLES, seed: int = DEFAULT_SEED, diagnostics: bool = True) -> dict:
    """One player-game's JSON record (see `_evaluate`), with its inputs' `fingerprint`."""
    return {**_evaluate(task, samples, seed, diagnostics), "fingerprint": fingerprint(task, samples, seed, diagnostics)}


def _evaluate(task: Task, samples: int, seed: int, diagnostics: bool) -> dict:
    """One player-game's JSON record: each Stat Distribution and both Score Distributions against
    the outcome, plus the Kalshi prices and (with `diagnostics`) the recalibration measurements.

    The PIT randomization draws from a generator seeded by (seed, player-game), so a record is
    the same whether it is computed alone, in a batch or in a resumed run.
    """
    pg, position = task.game, task.position
    by_stat: dict[str, list] = {}
    for market in to_prop_markets(pg.rungs):
        by_stat.setdefault(market.stat, []).append(market)
    base = dict(share=task.share, carry_share=task.carry_share)
    if not has_scoring_markets(by_stat):
        return _record(pg, position, NO_MARKETS, **base)
    if task.outcome is None:
        return _record(pg, position, DNP, **base)
    fair: dict[str, list] = {}
    fitted = fit_player(position, by_stat, task.share, prior_c=PRIOR_C,
                        on_fair_points=lambda stat, pts: fair.update({stat: pts}))
    if not fitted.dists:
        return _record(pg, position, UNFITTABLE, errors=fitted.errors, **base)
    rng = np.random.default_rng([seed, zlib.crc32(pg.key.encode())])
    complete = not any(f.startswith("incomplete") for f in fitted.flags)
    record = _record(pg, position, OK, flags=fitted.flags, complete=complete, no_stat_row=task.no_stat_row, **base)
    record["stats"] = {
        stat: _stat_record(stat, dist, pg, fair[stat], task.outcome, position, fitted, rng, diagnostics)
        for stat, dist in _evaluated(fitted).items()
    }
    record["points"] = _points_record(task, fitted, samples, seed, rng)
    if diagnostics:
        record["joint"] = _joint_draw(position, fitted, rng)
    return record


def _evaluated(fitted: Fitted) -> dict[str, StatDistribution]:
    """The Stat Distributions with Kalshi Rungs: the fitted market stats, carries, the TD total."""
    out = {s: d for s, d in fitted.dists.items() if s not in ("rushing_tds", "receiving_tds")}
    out.update(fitted.usage)
    if fitted.tds is not None:
        out[TD_STAT] = fitted.tds
    return out


def _sf(dist: StatDistribution, k) -> float:
    return float(np.asarray(dist.sf(np.array([k])))[0])


def _bounds(stat: str, dist: StatDistribution) -> tuple[int, int]:
    lower = -30 if stat in YARDS_STATS else 0
    upper = int(np.asarray(dist.ppf(np.array([1 - 1e-7])))[0]) + 1
    return lower, upper


def _logit_rmse(model: Sequence[float], fair: Sequence[float]) -> float:
    m = logit(np.clip(np.asarray(model, dtype=float), PROB_FLOOR, 1 - PROB_FLOOR))
    f = logit(np.clip(np.asarray(fair, dtype=float), PROB_FLOOR, 1 - PROB_FLOOR))
    return float(np.sqrt(np.mean((m - f) ** 2)))


def _scores(stat: str, dist: StatDistribution, y: int, thresholds, fair_probs, rng) -> dict:
    """PIT, CRPS, S(k) at the Rungs and the fit residual of one Stat Distribution."""
    lower, upper = _bounds(stat, dist)
    model = [_sf(dist, k) for k in thresholds]
    return {
        "mean": float(dist.mean),
        "pit": metrics.randomized_pit(dist, y, rng),
        "crps": metrics.crps_integer(dist, y, upper=upper, lower=lower),
        "model": model,
        "resid_rmse": _logit_rmse(model, fair_probs),
    }


def _stat_record(stat, dist, pg: PlayerGame, points, outcome, position, fitted: Fitted, rng, diagnostics) -> dict:
    rungs = sorted((r for r in pg.rungs if r.stat == stat), key=lambda r: r.threshold)
    fair_at = {p.threshold: p.prob for p in points}
    thresholds = [r.threshold for r in rungs]
    fair_probs = [fair_at[k] for k in thresholds]
    y = _outcome(stat, outcome)
    rec = {"y": y, "k": thresholds, "mid": [r.mid for r in rungs], "fair": fair_probs,
           "p_neg": prob_negative(dist), "p_zero": prob_zero(dist),
           **_scores(stat, dist, y, thresholds, fair_probs, rng)}
    if stat in YARDS_STATS:
        rec["sd"] = float(dist.sd)
        rec["pi0"] = float(dist.pi0)
        rec["sd_rel"] = _sd_relation(stat, float(dist.mean), position, float(dist.pi0))
    if diagnostics and stat in YARDS_STATS:
        rec.update(_yards_diagnostics(stat, points, position, fitted, y, thresholds, fair_probs, rng))
    return rec


def _sd_relation(stat: str, mu: float, position: str, pi0: float) -> float:
    relation = {"receiving_yards": receiving_yards_sd_relation, "rushing_yards": rushing_yards_sd_relation,
                "passing_yards": passing_yards_sd_relation}[stat]
    return float(relation(mu, position, pi0))


def _yards_diagnostics(stat, points, position, fitted: Fitted, y, thresholds, fair_probs, rng) -> dict:
    """The free-fit sd (no sd prior, ≥ FREE_SD_MIN_RUNGS Rungs) and a lognormal refit, each scored."""
    receptions = fitted.dists.get("receptions")
    pi0 = None if receptions is None else prob_zero(receptions)
    out = {}
    variants = []
    if len(points) >= FREE_SD_MIN_RUNGS:
        variants.append(("free", {"sd_prior": 0.0}))
    if stat != "passing_yards":
        variants.append(("lognormal", {"family": "lognormal"}))
    for name, kwargs in variants:
        try:
            dist = fit_yards(stat, points, position, pi0, **kwargs)
        except ValueError as error:  # FitError included
            out[name] = {"error": str(error)}
            continue
        out[name] = {"sd": float(dist.sd), "pi0": float(dist.pi0),
                     "sd_rel": _sd_relation(stat, float(dist.mean), position, float(dist.pi0)),
                     **_scores(stat, dist, y, thresholds, fair_probs, rng)}
    return out


def _points_record(task: Task, fitted: Fitted, samples: int, seed: int, rng) -> dict:
    y = fantasy_points(task.outcome, RULES)
    out: dict = {"y": y, "samples": samples}
    if task.line is not None:
        out["line"], out["line_mid"] = task.line
    if task.market_mean is not None:
        out["market_mean"] = task.market_mean
    for name, kind in (("copula", None), ("independent", NO_CORRELATION)):
        points = score_player(task.position, fitted, RULES, correlation=kind, n=samples, seed=seed).points
        q = np.quantile(points, [p / 100 for p in PERCENTILES], method="inverted_cdf")
        model = {"pct": dict(zip(map(str, PERCENTILES), map(float, q))),
                 "mean": float(points.mean()),
                 "capped_mean": float(np.clip(points, 0, 100).mean()),
                 "pit": metrics.pit_from_samples(points, y, rng),
                 "crps": metrics.crps_from_samples(points, y)}
        if task.line is not None:
            model["p_over"] = float(np.mean(points > task.line[0]))
        out[name] = model
    return out


# Stat pairs whose PIT dependence the report checks against the copula.
JOINT_PAIRS = (("receptions", "receiving_yards"), ("rushing_yards", "receptions"), ("rushing_yards", "receiving_yards"))


def _joint_draw(position: str, fitted: Fitted, rng) -> dict:
    """One joint draw of the player's stats from the copula, as randomized PITs under their own
    margins: across player-games, the correlation of these is what the model expects of the
    outcomes' PITs (the randomization and Rec = 0 rule weaken it below the latent rho)."""
    dists = stat_distributions(position, fitted)
    wanted = {s for pair in JOINT_PAIRS for s in pair if s in dists}
    if len(wanted) < 2:
        return {}
    draw = sample_joint(dists, 1, rng, params.correlation(position))
    return {s: metrics.randomized_pit(dists[s], int(draw[s][0]), rng) for s in sorted(wanted)}


# --- the run ----------------------------------------------------------------------------


def read_cache(path: Path) -> dict[str, dict]:
    """The records already in a JSONL cache, by key (a truncated last line is ignored)."""
    out = {}
    path = Path(path)
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        out[record["key"]] = record
    return out


def _evaluate_star(args):
    return evaluate(*args)


def run(
    todo: Sequence[Task],
    cache: Path,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    diagnostics: bool = True,
    jobs: int = 1,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    """Evaluate every Task not already in the JSONL `cache` with the same inputs (`fingerprint`),
    appending each record as it is done; returns the records of all of `todo`, in `todo` order.
    A later line for the same player-game supersedes an earlier one."""
    cache = Path(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    held = read_cache(cache)
    pending = [t for t in todo
               if held.get(t.game.key, {}).get("fingerprint") != fingerprint(t, samples, seed, diagnostics)]
    say = progress or (lambda msg: log.info(msg))
    say(f"{len(todo)} player-games, {len(todo) - len(pending)} cached, {len(pending)} to evaluate")
    start, done = time.monotonic(), 0
    args = [(t, samples, seed, diagnostics) for t in pending]
    with open(cache, "a") as f:
        if jobs > 1:
            pool = ProcessPoolExecutor(jobs)
            results = pool.map(_evaluate_star, args, chunksize=8)
        else:
            pool, results = None, map(_evaluate_star, args)
        try:
            for record in results:
                f.write(json.dumps(record) + "\n")
                held[record["key"]] = record
                done += 1
                if done % 200 == 0 or done == len(pending):
                    f.flush()
                    rate = done / (time.monotonic() - start)
                    say(f"{done}/{len(pending)} evaluated ({rate:.1f}/s, ~{(len(pending) - done) / rate / 60:.0f} min left)")
        finally:
            if pool is not None:
                pool.shutdown(cancel_futures=True)
    return [held[t.game.key] for t in todo]


# --- summaries --------------------------------------------------------------------------

STAT_LABELS = {"receptions": "Rec", "receiving_yards": "RecYd", "rushing_yards": "RuYd", "carries": "Car",
               "passing_yards": "PaYd", "passing_tds": "PaTD", "passing_interceptions": "Int", TD_STAT: "TD"}


def _pit_summary(pits) -> dict:
    h = metrics.pit_histogram(pits)
    return {"n": h.n, "counts": h.counts.tolist(), "chi2_p": h.chi2_p, "ks_p": h.ks_p}


def _rel_summary(p, o) -> dict:
    r = metrics.reliability(np.asarray(p), np.asarray(o))
    return {"brier": r.brier, "reliability": r.reliability, "resolution": r.resolution,
            "mean_p": float(np.mean(p)), "observed": float(np.mean(o)),
            "bins": [[None if math.isnan(a) else a, None if math.isnan(b) else b, int(c)]
                     for a, b, c in zip(r.mean_predicted, r.observed, r.counts)]}


def stat_summary(stat_records: Sequence[dict]) -> dict:
    """Pooled metrics of one stat's records (each a record["stats"][stat])."""
    if not stat_records:
        return {"n": 0}
    model_p, market_p, hits, sq = [], [], [], []
    for s in stat_records:
        for k, mid, fair, model in zip(s["k"], s["mid"], s["fair"], s["model"]):
            model_p.append(model)
            market_p.append(mid)
            hits.append(float(s["y"] >= k))
            sq.append((logit(np.clip(model, PROB_FLOOR, 1 - PROB_FLOOR))
                       - logit(np.clip(fair, PROB_FLOOR, 1 - PROB_FLOOR))) ** 2)
    out = {
        "n": len(stat_records),
        "rungs": len(hits),
        "pit": _pit_summary([s["pit"] for s in stat_records]),
        "crps": float(np.mean([s["crps"] for s in stat_records])),
        "mean_error": float(np.mean([s["y"] - s["mean"] for s in stat_records])),
        "resid_rmse": float(np.sqrt(np.mean(sq))) if sq else None,
    }
    if hits:
        out["model"] = _rel_summary(model_p, hits)
        out["market"] = _rel_summary(market_p, hits)
    return out


def _variant_summary(stat_records: Sequence[dict], name: str) -> dict | None:
    rows = [s for s in stat_records if name in s and "pit" in s[name]]
    if not rows:
        return None
    return {"n": len(rows),
            "pit": _pit_summary([s[name]["pit"] for s in rows]),
            "crps": float(np.mean([s[name]["crps"] for s in rows])),
            "crps_base": float(np.mean([s["crps"] for s in rows])),
            "pit_base": _pit_summary([s["pit"] for s in rows]),
            "resid_rmse": float(np.sqrt(np.mean([s[name]["resid_rmse"] ** 2 for s in rows]))),
            "resid_rmse_base": float(np.sqrt(np.mean([s["resid_rmse"] ** 2 for s in rows])))}


def points_summary(records: Sequence[dict], model: str) -> dict:
    """Fantasy-point metrics of one Score Distribution ("copula" or "independent") over records."""
    if not records:
        return {"n": 0}
    pts = [r["points"] for r in records]
    y = np.array([p["y"] for p in pts])
    pct = {k: np.array([p[model]["pct"][str(k)] for p in pts]) for k in PERCENTILES}
    out = {
        "n": len(pts),
        "cov90": metrics.coverage(pct[5], pct[95], y),
        "cov50": metrics.coverage(pct[25], pct[75], y),
        "below_p5": float(np.mean(y < pct[5])),
        "above_p95": float(np.mean(y > pct[95])),
        "pit": _pit_summary([p[model]["pit"] for p in pts]),
        "crps": float(np.mean([p[model]["crps"] for p in pts])),
        "pinball": float(np.mean([metrics.pinball_loss(pct[k], y, k / 100) for k in PERCENTILES])),
        "mean_error": float(np.mean(y - np.array([p[model]["mean"] for p in pts]))),
    }
    lined = [p for p in pts if "line" in p]
    if lined:
        o = np.array([float(p["y"] > p["line"]) for p in lined])
        ours = np.array([p[model]["p_over"] for p in lined])
        market = np.array([p["line_mid"] for p in lined])
        eps = 1 / (2 * pts[0]["samples"])  # a Monte Carlo probability of 0 or 1 is at most this far off
        out["line"] = {"n": len(lined), "log_loss": metrics.log_loss(ours, o, eps=eps), "brier": float(np.mean((ours - o) ** 2)),
                       "market_log_loss": metrics.log_loss(market, o), "market_brier": float(np.mean((market - o) ** 2)),
                       "mean_p": float(ours.mean()), "market_mean_p": float(market.mean()), "observed": float(o.mean())}
    meaned = [p for p in pts if "market_mean" in p]
    if meaned:
        actual = np.clip([p["y"] for p in meaned], 0, 100)
        out["mean"] = {"n": len(meaned),
                       "mae": float(np.mean(np.abs(np.array([p[model]["capped_mean"] for p in meaned]) - actual))),
                       "market_mae": float(np.mean(np.abs(np.array([p["market_mean"] for p in meaned]) - actual))),
                       "bias": float(np.mean(actual - np.array([p[model]["capped_mean"] for p in meaned]))),
                       "market_bias": float(np.mean(actual - np.array([p["market_mean"] for p in meaned])))}
    return out


def _by(records, *keys):
    groups = defaultdict(list)
    for r in records:
        groups[tuple(r[k] for k in keys)].append(r)
    return dict(sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0])))


def _mu_bucket(mu: float, edges: Sequence[float]) -> str:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= mu < hi:
            return f"{lo:g}–{hi:g}"
    return f"{edges[-1]:g}+"


MU_EDGES = {"receiving_yards": (0, 25, 40, 55, 70), "rushing_yards": (0, 20, 40, 60, 80),
            "passing_yards": (0, 200, 230, 260)}


def summarize(records: Sequence[dict]) -> dict:
    """The report's numbers from the records: counts, per-stat calibration, fantasy points, diagnostics."""
    ok = [r for r in records if r["status"] == OK]
    summary: dict = {"counts": _counts(records)}

    # Per stat, by position x season, and pooled.
    stats = defaultdict(list)
    for r in ok:
        for stat, s in r["stats"].items():
            stats[stat].append({**s, "position": r["position"], "season": r["season"]})
    per_stat = {}
    for stat, rows in stats.items():
        per_stat[stat] = {"all": stat_summary(rows)}
        for (season,), group in _by(rows, "season").items():
            per_stat[stat][f"{season}"] = stat_summary(group)
        for (position, season), group in _by(rows, "position", "season").items():
            per_stat[stat][f"{position} {season}"] = stat_summary(group)
    summary["stats"] = per_stat

    # Fantasy points.
    fp = {}
    groups = {"all": ok, **{f"{s}": g for (s,), g in _by(ok, "season").items()}}
    for (pos, season), g in _by(ok, "position", "season").items():
        groups[f"{pos} {season}"] = g
    for (season, complete), g in _by(ok, "season", "complete").items():
        groups[f"{season} {'complete' if complete else 'incomplete'}"] = g
    for name, g in groups.items():
        fp[name] = {m: points_summary(g, m) for m in ("copula", "independent")}
    summary["points"] = fp

    summary["dispersion"] = _dispersion(stats)
    summary["shift"] = _shift(stats)
    summary["lognormal"] = {f"{stat} {pos}": v for stat in ("receiving_yards", "rushing_yards")
                            for (pos,), g in _by(stats.get(stat, []), "position").items()
                            if (v := _variant_summary(g, "lognormal"))}
    summary["free_fit"] = {f"{stat} {pos}": v for stat in YARDS_STATS
                           for (pos,), g in _by(stats.get(stat, []), "position").items()
                           if (v := _variant_summary(g, "free"))}
    summary["joint"] = _joint(ok)
    summary["td"] = _td(ok)
    return summary


def _counts(records) -> dict:
    by_status = defaultdict(int)
    by_season = defaultdict(lambda: defaultdict(int))
    for r in records:
        by_status[r["status"]] += 1
        by_season[r["season"]][r["status"]] += 1
    ok = [r for r in records if r["status"] == OK]
    return {"player_games": len(records), "status": dict(by_status),
            "by_season": {str(k): dict(v) for k, v in sorted(by_season.items())},
            "ok_by_position": {f"{p} {s}": len(g) for (p, s), g in _by(ok, "position", "season").items()},
            "games": len({r["game_id"] for r in records}), "players": len({r["gsis_id"] for r in records}),
            "no_stat_row": sum(bool(r.get("no_stat_row")) for r in ok),
            "flags": dict(_flag_counts(ok))}


def _flag_counts(ok):
    counts = defaultdict(int)
    for r in ok:
        for f in r.get("flags", []):
            counts[f.split(" (")[0]] += 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def _geo(ratios) -> dict:
    r = np.asarray(ratios, dtype=float)
    r = r[np.isfinite(r) & (r > 0)]
    if not len(r):
        return {"n": 0}
    return {"n": int(len(r)), "geo_mean": float(np.exp(np.mean(np.log(r)))), "median": float(np.median(r)),
            "p25": float(np.quantile(r, 0.25)), "p75": float(np.quantile(r, 0.75))}


def _dispersion(stats) -> dict:
    """Free-fit sd / the relation's sd (at the free fit's mu and pi0), and the production fit's
    sd / relation, by stat x position and by mu bucket."""
    out = {}
    for stat in YARDS_STATS:
        rows = [s for s in stats.get(stat, []) if "free" in s and "sd" in s["free"]]
        for (pos,), g in _by(rows, "position").items():
            out[f"{stat} {pos}"] = {"free": _geo([s["free"]["sd"] / s["free"]["sd_rel"] for s in g]),
                                    "fitted": _geo([s["sd"] / s["sd_rel"] for s in g])}
            for bucket, gb in _by([{**s, "b": _mu_bucket(s["mean"], MU_EDGES[stat])} for s in g], "b").items():
                out[f"{stat} {pos} mu {bucket[0]}"] = {"free": _geo([s["free"]["sd"] / s["free"]["sd_rel"] for s in gb])}
    return out


def _shift(stats) -> dict:
    """Model P(Y < 0) and P(Y = 0) against the observed frequency, rushing yards by position x mu."""
    out = {}
    for (pos,), g in _by(stats.get("rushing_yards", []), "position").items():
        for bucket, gb in [(("all",), g), *_by([{**s, "b": _mu_bucket(s["mean"], MU_EDGES["rushing_yards"])}
                                                for s in g], "b").items()]:
            out[f"{pos} mu {bucket[0]}"] = {
                "n": len(gb),
                "p_neg": float(np.mean([s["p_neg"] for s in gb])), "obs_neg": float(np.mean([s["y"] < 0 for s in gb])),
                "p_zero": float(np.mean([s["p_zero"] for s in gb])), "obs_zero": float(np.mean([s["y"] == 0 for s in gb])),
                "obs_le_minus5": float(np.mean([s["y"] <= -5 for s in gb])),
            }
    return out


def _normal_score_corr(a, b) -> float | None:
    if len(a) < 10:
        return None
    za, zb = ndtri(np.clip(a, 1e-9, 1 - 1e-9)), ndtri(np.clip(b, 1e-9, 1 - 1e-9))
    return float(np.corrcoef(za, zb)[0, 1])


def _joint(ok) -> dict:
    """Normal-score correlation of the outcomes' PITs per stat pair, against the same statistic
    from one copula draw per player-game (what the model expects) and the latent rho."""
    out = {}
    for pos in MODELED_POSITIONS:
        rows = [r for r in ok if r["position"] == pos]
        subsets = {"all": rows}
        if pos == "RB":
            subsets["workhorse"] = [r for r in rows if (r.get("carry_share") or 0) >= WORKHORSE_SHARE]
            subsets["non-workhorse"] = [r for r in rows if r.get("carry_share") is not None
                                        and r["carry_share"] < WORKHORSE_SHARE]
        stats_order, matrix = params.correlation(pos)
        for a, b in JOINT_PAIRS:
            if a not in stats_order or b not in stats_order:
                continue
            rho = float(matrix[stats_order.index(a)][stats_order.index(b)])
            for name, group in subsets.items():
                both = [r for r in group if a in r["stats"] and b in r["stats"]]
                sim = [r for r in both if a in r.get("joint", {}) and b in r.get("joint", {})]
                observed = _normal_score_corr([r["stats"][a]["pit"] for r in both], [r["stats"][b]["pit"] for r in both])
                if observed is None:
                    continue
                out[f"{pos} {name} {STAT_LABELS[a]}–{STAT_LABELS[b]}"] = {
                    "n": len(both), "observed": observed, "latent_rho": rho,
                    "model": _normal_score_corr([r["joint"][a] for r in sim], [r["joint"][b] for r in sim]),
                    "se": 1 / math.sqrt(max(len(both) - 3, 1))}
    return out


def _td(ok) -> dict:
    """Kalshi's TD Rung mids against the realized frequency, by threshold x season x position."""
    rows = defaultdict(lambda: ([], [], []))
    for r in ok:
        s = r["stats"].get(TD_STAT)
        if not s:
            continue
        for k, mid, model in zip(s["k"], s["mid"], s["model"]):
            for name in (f"{k}+ {r['season']}", f"{k}+ {r['season']} {r['position']}", f"{k}+ all"):
                rows[name][0].append(mid)
                rows[name][1].append(float(s["y"] >= k))
                rows[name][2].append(model)
    return {name: {"n": len(m), "mid": float(np.mean(m)), "observed": float(np.mean(o)), "model": float(np.mean(md)),
                   "brier_mid": float(np.mean((np.array(m) - np.array(o)) ** 2)),
                   "se": float(np.sqrt(np.mean(o) * (1 - np.mean(o)) / len(o)))}
            for name, (m, o, md) in sorted(rows.items())}


# --- Markdown ---------------------------------------------------------------------------


def _f(x, digits=3) -> str:
    if x is None:
        return "–"
    if isinstance(x, float) and math.isnan(x):
        return "–"
    return f"{x:.{digits}f}"


def _pct(x) -> str:
    return "–" if x is None else f"{100 * x:.1f}%"


def _hist(counts) -> str:
    n = sum(counts) or 1
    return " ".join(f"{round(100 * c / n):d}" for c in counts)


def render_tables(summary: dict) -> str:
    """The summary as Markdown tables (the report's data sections)."""
    out = []
    c = summary["counts"]
    out += ["### Counts", "", f"Player-games with Kalshi Rungs: {c['player_games']} ({c['games']} Games, "
            f"{c['players']} players).", "", "| Season | " + " | ".join(sorted({k for v in c['by_season'].values() for k in v}))
            + " |", "|---" * (1 + len({k for v in c['by_season'].values() for k in v})) + "|"]
    statuses = sorted({k for v in c["by_season"].values() for k in v})
    for season, v in c["by_season"].items():
        out.append(f"| {season} | " + " | ".join(str(v.get(s, 0)) for s in statuses) + " |")
    out += ["", f"Scored with an all-zero outcome (offensive snaps, no nflverse stat row): {c['no_stat_row']}.",
            "", "Scored player-games by position: " + ", ".join(f"{k} {v}" for k, v in c["ok_by_position"].items()) + ".",
            "", "Flags on scored player-games: " + "; ".join(f"{k} ×{v}" for k, v in c["flags"].items()) + ".", ""]

    out += ["### Fantasy points (Sleeper)", "",
            "| Group | Model | n | P5–P95 cover | P25–P75 cover | < P5 | > P95 | PIT χ² p | CRPS | pinball | bias (y − mean) |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, models in summary["points"].items():
        for m, s in models.items():
            if not s.get("n"):
                continue
            out.append(f"| {name} | {m} | {s['n']} | {_pct(s['cov90'])} | {_pct(s['cov50'])} | {_pct(s['below_p5'])} | "
                       f"{_pct(s['above_p95'])} | {_f(s['pit']['chi2_p'])} | {_f(s['crps'], 2)} | {_f(s['pinball'], 3)} | "
                       f"{_f(s['mean_error'], 2)} |")
    out += ["", "PIT histograms (% per decile, 0→1):", ""]
    for name, models in summary["points"].items():
        for m, s in models.items():
            if s.get("n"):
                out.append(f"- {name} {m}: {_hist(s['pit']['counts'])}")
    out += ["", "### Model vs market on fantasy points", "",
            "KXNFLFFPTS: P(points > line). Log loss and Brier against the outcome (lower is better).", "",
            "| Group | n | ours log loss | indep. log loss | market log loss | ours Brier | indep. Brier | market Brier | mean p ours / market / observed |",
            "|---|---|---|---|---|---|---|---|---|"]
    for name, models in summary["points"].items():
        cop, ind = models["copula"].get("line"), models["independent"].get("line")
        if cop:
            out.append(f"| {name} | {cop['n']} | {_f(cop['log_loss'])} | {_f(ind['log_loss'])} | {_f(cop['market_log_loss'])} | "
                       f"{_f(cop['brier'])} | {_f(ind['brier'])} | {_f(cop['market_brier'])} | "
                       f"{_f(cop['mean_p'], 2)} / {_f(cop['market_mean_p'], 2)} / {_f(cop['observed'], 2)} |")
    out += ["", "KXNFLFFPTSLADDER: expected points (capped 0–100) against actual.", "",
            "| Group | n | ours MAE | indep. MAE | market MAE | ours bias | market bias |", "|---|---|---|---|---|---|---|"]
    for name, models in summary["points"].items():
        cop, ind = models["copula"].get("mean"), models["independent"].get("mean")
        if cop:
            out.append(f"| {name} | {cop['n']} | {_f(cop['mae'], 2)} | {_f(ind['mae'], 2)} | {_f(cop['market_mae'], 2)} | "
                       f"{_f(cop['bias'], 2)} | {_f(cop['market_bias'], 2)} |")

    out += ["", "### Per-stat calibration", "",
            "PIT χ² p: uniformity of the randomized PIT (10 bins). Rungs: Brier and reliability (lower is "
            "better) of our fitted P(Y ≥ k) and of the raw Kalshi mid at Kalshi's own thresholds. "
            "Fit residual: RMSE in logit of our S(k) against the mids we fitted to.", "",
            "| Stat | Group | n | PIT χ² p | PIT % per decile | CRPS | bias | Rungs | Brier ours / mid | reliability ours / mid | mean p ours / mid / observed | fit resid |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for stat, groups in summary["stats"].items():
        for name, s in groups.items():
            if not s.get("n"):
                continue
            mo, mk = s.get("model"), s.get("market")
            out.append(f"| {STAT_LABELS.get(stat, stat)} | {name} | {s['n']} | {_f(s['pit']['chi2_p'])} | "
                       f"{_hist(s['pit']['counts'])} | {_f(s['crps'], 2)} | {_f(s['mean_error'], 2)} | {s['rungs']} | "
                       + (f"{_f(mo['brier'], 4)} / {_f(mk['brier'], 4)} | {_f(mo['reliability'], 4)} / {_f(mk['reliability'], 4)} | "
                          f"{_f(mo['mean_p'], 3)} / {_f(mk['mean_p'], 3)} / {_f(mo['observed'], 3)}" if mo else "– | – | –")
                       + f" | {_f(s['resid_rmse'], 3)} |")
    out += ["", "Reliability diagrams (pooled per stat; bin: mean predicted → observed, n):", ""]
    for stat, groups in summary["stats"].items():
        s = groups["all"]
        for who in ("model", "market"):
            if who in s:
                cells = [f"{_f(a, 2)}→{_f(b, 2)} ({n})" for a, b, n in s[who]["bins"] if n]
                out.append(f"- {STAT_LABELS.get(stat, stat)} {'ours' if who == 'model' else 'Kalshi mid'}: " + ", ".join(cells))

    out += ["", "### Dispersion: sd / relation sd", "",
            "Free fit: no sd prior, players with ≥ 4 Rungs. Fitted: the production fit (prior λ = 0.44).", "",
            "| Stat, position, mu | n | free: geo mean | median | p25–p75 | fitted: geo mean |", "|---|---|---|---|---|---|"]
    for name, d in summary["dispersion"].items():
        fr = d["free"]
        if fr.get("n"):
            out.append(f"| {name} | {fr['n']} | {_f(fr['geo_mean'])} | {_f(fr['median'])} | {_f(fr['p25'], 2)}–{_f(fr['p75'], 2)} | "
                       f"{_f(d.get('fitted', {}).get('geo_mean'))} |")
    out += ["", "Free fit vs production fit, scored (same player-games):", "",
            "| Stat, position | n | CRPS free / prod | PIT χ² p free / prod | fit resid free / prod |", "|---|---|---|---|---|"]
    for name, v in summary["free_fit"].items():
        out.append(f"| {name} | {v['n']} | {_f(v['crps'], 3)} / {_f(v['crps_base'], 3)} | {_f(v['pit']['chi2_p'])} / "
                   f"{_f(v['pit_base']['chi2_p'])} | {_f(v['resid_rmse'])} / {_f(v['resid_rmse_base'])} |")

    out += ["", "### Rushing yards below and at zero", "",
            "| Position, mu | n | model P(Y<0) | observed | model P(Y=0) | observed | observed P(Y ≤ −5) |", "|---|---|---|---|---|---|---|"]
    for name, s in summary["shift"].items():
        out.append(f"| {name} | {s['n']} | {_pct(s['p_neg'])} | {_pct(s['obs_neg'])} | {_pct(s['p_zero'])} | {_pct(s['obs_zero'])} | "
                   f"{_pct(s['obs_le_minus5'])} |")

    out += ["", "### Gamma vs lognormal nonzero part", "", "Same fit (production prior and pi0), only the family changes.", "",
            "| Stat, position | n | CRPS lognormal / gamma | PIT χ² p lognormal / gamma | fit resid lognormal / gamma | PIT lognormal |",
            "|---|---|---|---|---|---|"]
    for name, v in summary["lognormal"].items():
        out.append(f"| {name} | {v['n']} | {_f(v['crps'], 3)} / {_f(v['crps_base'], 3)} | {_f(v['pit']['chi2_p'])} / "
                   f"{_f(v['pit_base']['chi2_p'])} | {_f(v['resid_rmse'])} / {_f(v['resid_rmse_base'])} | {_hist(v['pit']['counts'])} |")

    out += ["", "### Joint calibration: PIT dependence", "",
            "Normal-score correlation of the outcomes' randomized PITs, against the same statistic for one copula "
            "draw per player-game (what the copula expects) and the latent rho. Independence expects 0. ±se ≈ 1/√(n−3).", "",
            "| Pair | n | observed | copula expects | latent rho | se |", "|---|---|---|---|---|---|"]
    for name, v in summary["joint"].items():
        out.append(f"| {name} | {v['n']} | {_f(v['observed'])} | {_f(v['model'])} | {_f(v['latent_rho'])} | {_f(v['se'])} |")

    out += ["", "### TD Rungs: Kalshi mid vs realized", "",
            "| Rung, group | n | mean mid | observed | ±se | our model | Brier (mid) |", "|---|---|---|---|---|---|---|"]
    for name, v in summary["td"].items():
        out.append(f"| {name} | {v['n']} | {_pct(v['mid'])} | {_pct(v['observed'])} | {_pct(v['se'])} | {_pct(v['model'])} | {_f(v['brier_mid'], 4)} |")
    return "\n".join(out) + "\n"


# --- loading the real inputs (network through nflreadpy, cached by it) -------------------


def load_context(root: Path, seasons: Sequence[int], stats_fetch=None, rosters_fetch=None,
                 snaps_fetch=None) -> Context:
    """The Context for Kalshi's cache at `root`: nflverse weekly player stats, rosters and snap
    counts for `seasons` (plus the TD-history seasons before them), Kalshi's fantasy-points records."""
    from ffp.outcomes import load_outcomes
    from ffp.sources.kalshi import load_fantasy_lines, load_fantasy_means
    from ffp.td_share import load_td_history

    stats_fetch = stats_fetch or _nflreadpy_player_stats
    rosters_fetch = rosters_fetch or _nflreadpy_rosters_weekly
    snaps_fetch = snaps_fetch or _nflreadpy_snap_counts
    prior = int(params.load("base_rates")["td_share"]["prior_seasons"])
    rows: dict[int, list] = {}

    def fetch(season: int) -> list:
        if season not in rows:
            rows[season] = list(stats_fetch(season))
        return rows[season]

    outcomes = load_outcomes(seasons, fetch=fetch)
    shares = {}
    for s in seasons:
        shares.update(trailing_carry_shares(fetch(s)))
    history_cache: dict[tuple[int, int], Mapping] = {}

    def td_history(season: int, week: int):
        if (season, week) not in history_cache:
            history_cache[season, week] = load_td_history(season, fetch=fetch, before=(season, week))
        return history_cache[season, week]

    for s in range(min(seasons) - prior, max(seasons) + 1):
        fetch(s)
    roster = [row for s in seasons for row in rosters_fetch(s)]
    played = played_games([row for s in seasons for row in snaps_fetch(s)], roster)
    key = lambda r: (r.game_id, r.gsis_id)  # noqa: E731
    return Context(Positions(roster), outcomes, td_history, shares,
                   {key(r): r for r in load_fantasy_lines(root)}, {key(r): r for r in load_fantasy_means(root)},
                   played)


def _nflreadpy_player_stats(season: int) -> list[dict]:  # pragma: no cover - network
    import nflreadpy

    return nflreadpy.load_player_stats(season, summary_level="week").to_dicts()


def _nflreadpy_rosters_weekly(season: int) -> list[dict]:  # pragma: no cover - network
    import nflreadpy

    cols = ["season", "week", "gsis_id", "position", "pfr_id"]
    return nflreadpy.load_rosters_weekly(season).select(cols).to_dicts()


def _nflreadpy_snap_counts(season: int) -> list[dict]:  # pragma: no cover - network
    import nflreadpy

    return nflreadpy.load_snap_counts(season).select(["game_id", "pfr_player_id", "offense_snaps"]).to_dicts()


def to_json(summary: dict) -> str:
    return json.dumps(summary, indent=1, default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else str(o))


# --- the CLI (scripts/backtest_kalshi.py) ------------------------------------------------

DEFAULT_OUT = Path("data") / "backtest"  # git-ignored


def main(argv: Sequence[str] | None = None, stats_fetch=None, rosters_fetch=None, snaps_fetch=None) -> int:
    """Run the Kalshi Backtest; write records.jsonl (the resumable cache), summary.json and tables.md."""
    import argparse

    from ffp.sources.kalshi import DEFAULT_ROOT, drop_counts, load_rungs

    ap = argparse.ArgumentParser(prog="backtest_kalshi.py", description="Backtest the model on Kalshi's Ladders (S5.2).")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="the Kalshi cache (default data/kalshi)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default data/backtest)")
    ap.add_argument("--seasons", type=int, nargs="+", default=[2025, 2026])
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                    help=f"Monte Carlo samples per player (default {DEFAULT_SAMPLES})")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--jobs", type=int, default=1, help="worker processes (default 1)")
    ap.add_argument("--limit", type=int, help="only the first N player-games (a quick check)")
    ap.add_argument("--no-diagnostics", action="store_true",
                    help="skip the free-fit, lognormal and copula-draw diagnostics")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("ffp.distributions").setLevel(logging.ERROR)  # per-fit P(0) fallbacks: thousands of lines

    games = [pg for pg in player_games(load_rungs(args.root)) if pg.season in args.seasons][: args.limit]
    log.info("loading nflverse for %s", args.seasons)
    ctx = load_context(args.root, args.seasons, stats_fetch, rosters_fetch, snaps_fetch)
    todo, skipped = tasks(games, ctx)
    records = run(todo, args.out / "records.jsonl", args.samples, args.seed, not args.no_diagnostics, args.jobs)
    summary = summarize(records + skipped)
    summary["kalshi_drops"] = {f"{series} {reason}": n for (series, reason), n in sorted(drop_counts(args.root).items())}
    summary["run"] = {"samples": args.samples, "seed": args.seed, "seasons": args.seasons, "root": str(args.root)}
    (args.out / "summary.json").write_text(to_json(summary))
    (args.out / "tables.md").write_text(render_tables(summary))
    status = summary["counts"]["status"]
    print("player-games: " + ", ".join(f"{k} {v}" for k, v in sorted(status.items())))
    print(f"wrote {args.out / 'summary.json'} and {args.out / 'tables.md'}")
    return 0
