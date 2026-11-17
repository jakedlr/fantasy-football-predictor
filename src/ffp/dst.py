"""D/ST (S6.4): a D/ST's Score Distribution from Analog Games, tilted to its markets.

A D/ST's points come from a dozen correlated parts: Points Allowed against sacks and turnovers
against Yards Allowed, scored through non-linear tiers. Rather than model each part and their
correlations, the model draws whole past team-games (params/dst_games.json, 2015 on, built by
scripts/build_params.py dst_games) and scores each under the Scoring Rules, so tiers, whole
football scores and every correlation come for free (research/kickers-dst.md; docs/adr/0003):

1. Analog Games: each past team-game is weighted by a Gaussian kernel in the opponent's Implied
   Team Total (bandwidth BANDWIDTH points, widened until the effective sample reaches MIN_ESS)
   times a recency weight that halves every HALF_LIFE seasons.
2. Market tilt: where a Book prices the opponent's team total (its points are this D/ST's Points
   Allowed, before any exclusion) or this D/ST scoring a TD, the weights are exponentially tilted
   so the Analog Games reproduce those Fair Probabilities (least squares in logit, ridge on the
   tilt, so the market moves the analogs rather than replaces them).
3. Draw rows by weight and score them.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

import numpy as np
from scipy import optimize
from scipy.special import logit

from ffp import params
from ffp.fair import FairPoint
from ffp.scoring import ScoringRules, points_allowed, score_dst
from ffp.simulate import DEFAULT_SAMPLES, DEFAULT_SEED, ScoreDistribution

BANDWIDTH = 1.5  # points of opponent Implied Team Total
MIN_ESS = 150.0
HALF_LIFE = 8.0  # seasons: a season's Analog Games count half as much as those HALF_LIFE seasons later
RIDGE = 0.01  # penalty on the tilt coefficients
PROB_RANGE = (0.02, 0.98)  # Fair Probabilities outside this say little and are left out of the tilt
RETURN_TDS = ("interception_return_tds", "fumble_return_tds", "kickoff_return_tds", "punt_return_tds",
              "blocked_kick_return_tds")


@dataclass(frozen=True)
class AnalogTable:
    """The Analog Games, column by column."""

    columns: dict[str, np.ndarray]

    def __len__(self) -> int:
        return len(self.columns["season"])

    def rows(self, index: np.ndarray) -> dict[str, np.ndarray]:
        return {name: values[index] for name, values in self.columns.items()}

    @property
    def dst_tds(self) -> np.ndarray:
        return sum(self.columns[c] for c in RETURN_TDS)


@cache
def analog_table() -> AnalogTable:
    doc = params.load("dst_games")
    rows = doc["rows"]
    columns = {}
    for i, name in enumerate(doc["columns"]):
        values = [r[i] for r in rows]
        columns[name] = np.array(values) if isinstance(values[0], str) else np.array(values, dtype=float)
    return AnalogTable(columns)


def _ess(w: np.ndarray) -> float:
    return float(w.sum() ** 2 / np.sum(w * w))


def analog_weights(opp_itt: float, season: int, table: AnalogTable | None = None) -> np.ndarray:
    """Each Analog Game's weight (summing to 1) for a D/ST facing an opponent with Implied Team Total
    `opp_itt` in `season`."""
    table = table or analog_table()
    recency = 0.5 ** ((season - table.columns["season"]) / HALF_LIFE)
    h = BANDWIDTH
    for _ in range(30):
        w = recency * np.exp(-0.5 * ((table.columns["opp_itt"] - opp_itt) / h) ** 2)
        if w.sum() > 0 and _ess(w) >= MIN_ESS:
            break
        h *= 1.25
    return w / w.sum()


@dataclass(frozen=True)
class Target:
    """A Fair Probability the tilt should reproduce: P(feature >= threshold) = prob."""

    feature: np.ndarray  # per Analog Game
    threshold: float
    prob: float
    weight: float


def targets(table: AnalogTable, opponent_points: Sequence[FairPoint] = (),
            dst_tds: Sequence[FairPoint] = ()) -> list[Target]:
    """The tilt's targets: the opponent's team-total Fair Probabilities on Points Allowed (all
    points), and the D/ST TD Fair Probabilities on its return TDs; extreme ones left out."""
    out = []
    for feature, points in ((table.columns["points_allowed_all"], opponent_points), (table.dst_tds, dst_tds)):
        out += [Target(feature, p.threshold, p.prob, p.weight) for p in points if PROB_RANGE[0] < p.prob < PROB_RANGE[1]]
    return out


def tilt(weights: np.ndarray, goals: Sequence[Target]) -> np.ndarray:
    """`weights` exponentially tilted, q ∝ w·exp(F λ), λ minimising Σ wⱼ (logit Pⱼ(q) − logit pⱼ)² + RIDGE·|λ|²,
    with F the indicators feature ≥ threshold. No goals: the weights unchanged."""
    if not goals:
        return weights
    f = np.column_stack([(g.feature >= g.threshold).astype(float) for g in goals])
    goal = logit(np.array([g.prob for g in goals]))
    wt = np.array([g.weight for g in goals])
    log_w = np.log(np.maximum(weights, 1e-300))

    def q_of(lam):
        a = log_w + f @ lam
        q = np.exp(a - a.max())
        return q / q.sum()

    def objective(lam):
        q = q_of(lam)
        p = np.clip(q @ f, 1e-9, 1 - 1e-9)
        r = logit(p) - goal
        cov = (f * q[:, None]).T @ f - np.outer(p, p)
        grad = cov @ (2 * wt * r / (p * (1 - p))) + 2 * RIDGE * lam
        return float(np.sum(wt * r * r) + RIDGE * lam @ lam), grad

    result = optimize.minimize(objective, np.zeros(f.shape[1]), jac=True, method="L-BFGS-B")
    return q_of(result.x)


@dataclass(frozen=True)
class DstResult:
    scores: ScoreDistribution
    sampled: dict[str, np.ndarray]  # the drawn Analog Games' columns, plus "points_allowed" under the rules
    effective_games: float  # effective sample size of the final weights


def dst_scores(opp_itt: float, season: int, rules: ScoringRules, opponent_points: Sequence[FairPoint] = (),
               dst_tds: Sequence[FairPoint] = (), n: int = DEFAULT_SAMPLES, seed: int = DEFAULT_SEED,
               table: AnalogTable | None = None) -> DstResult:
    """The D/ST's Score Distribution under `rules` (see the module docstring)."""
    table = table or analog_table()
    q = tilt(analog_weights(opp_itt, season, table), targets(table, opponent_points, dst_tds))
    rng = np.random.default_rng(seed)
    drawn = table.rows(rng.choice(len(table), size=n, p=q))
    drawn["points_allowed"] = np.asarray(points_allowed(drawn, rules), dtype=float)
    drawn["dst_tds"] = sum(drawn[c] for c in RETURN_TDS)
    return DstResult(ScoreDistribution(np.asarray(score_dst(drawn, rules), dtype=float)), drawn, _ess(q))
