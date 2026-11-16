"""Stat Distributions: one Scoring Stat for one player in one Game."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy import optimize, stats
from scipy.special import expit, logit

from ffp import params
from ffp.fair import MAIN_LINE_WEIGHT, FairPoint


class FitError(ValueError):
    """The Fair Probabilities can't be fitted: none given, out of the model's reach, or no convergence.

    A problem with one player's market data, unlike a plain ValueError for a bad
    argument such as an unknown position.
    """


class StatDistribution(Protocol):
    """Probability distribution of one Scoring Stat. Methods accept and return numpy arrays."""

    def sf(self, x: np.ndarray) -> np.ndarray:
        """P(Y >= x) for integer x."""

    def ppf(self, u: np.ndarray) -> np.ndarray:
        """Inverse CDF: the stat value at cumulative probability u in (0, 1).

        Used for sampling (u uniform) and, from Sprint 3, by the copula.
        """

    @property
    def mean(self) -> float:
        """Expected value."""


class HurdleShiftedGamma:
    """Integer yards: a point mass pi0 at 0, else a discretised, zero-truncated shifted gamma.

    Construction (research/stat-distributions.md Q1a, Q4):
    - Continuous part X = G − shift, G ~ gamma with mean m + shift and sd `sd`,
      so X has mean m and sd `sd`.
    - Discretise: P(y) ∝ F(y + ½) − F(y − ½) for integer y, F the cdf of X.
      This is where the 0.5 continuity correction lives: P(Y >= x) for x >= 1 is
      the continuous tail beyond x − ½.
    - Zero-truncate: drop the bin y = 0 (mass Z = F(½) − F(−½)) and renormalise
      by 1 − Z. All of Y = 0 comes from the point mass pi0.
    - Negative yards: X >= −shift, so the bins y = −shift .. −1 keep the small mass
      the shifted gamma puts below −½ (real negative-yardage games). They are part
      of the nonzero distribution, so sf(x) = 1 for x <= −shift and the mean
      includes them. No floor is needed because nothing here takes a log-pmf.
    """

    def __init__(self, m: float, sd: float, pi0: float, shift: float):
        if m <= 0 or sd <= 0 or not 0 <= pi0 < 1:
            raise ValueError(f"invalid parameters m={m}, sd={sd}, pi0={pi0}")
        self.m, self.sd, self.pi0, self.shift = m, sd, pi0, shift
        g_mean = m + shift
        self._gamma = stats.gamma(a=(g_mean / sd) ** 2, scale=sd**2 / g_mean)
        self._zero_bin = self._gamma.cdf(shift + 0.5) - self._gamma.cdf(shift - 0.5)
        # Integer support: [lo, hi]; beyond hi the tail is below 1e-16.
        self._lo = -math.floor(shift)
        self._hi = math.ceil(self._gamma.isf(1e-16) - shift) + 1
        # Tail-sum formula for an integer variable: E[Y] = Σ_{x>=1} P(Y>=x) − Σ_{x<=0} P(Y<x).
        self._mean = float(
            np.sum(self.sf(np.arange(1, self._hi + 1))) - np.sum(1 - self.sf(np.arange(self._lo, 1)))
        )
        self._cdf = None  # P(Y <= y) on the support grid, built on first ppf call

    def sf(self, x: np.ndarray) -> np.ndarray:
        """P(Y >= x); a fractional x is rounded up (Y >= 69.5 means Y >= 70)."""
        x = np.ceil(np.asarray(x, dtype=float))
        tail = self._gamma.sf(x - 0.5 + self.shift)  # P(X >= x − ½)
        at_or_below_zero = x <= 0
        nz_sf = np.where(at_or_below_zero, tail - self._zero_bin, tail) / (1 - self._zero_bin)
        nz_sf = np.clip(nz_sf, 0.0, 1.0)
        return (1 - self.pi0) * nz_sf + self.pi0 * at_or_below_zero

    def ppf(self, u: np.ndarray) -> np.ndarray:
        """Smallest integer y with P(Y <= y) >= u."""
        ys = np.arange(self._lo, self._hi + 1)
        if self._cdf is None:
            self._cdf = 1 - self.sf(ys + 1)
        idx = np.searchsorted(self._cdf, np.asarray(u, dtype=float), side="left")
        return ys[np.minimum(idx, len(ys) - 1)]

    @property
    def mean(self) -> float:
        return self._mean


@dataclass(frozen=True)
class _ReceivingYardsParams:
    c0: float  # pi0 = expit(c0 + c1 ln mu), mu the overall mean
    c1: float
    a: float  # dispersion relation of the nonzero part: sd_rel(m) = a * m**b
    b: float


# research/stat-distributions.md Q2 two-part table / Q4 recommendation table.
RECEIVING_YARDS_PARAMS = {
    "WR": _ReceivingYardsParams(c0=6.75, c1=-2.48, a=3.00, b=0.622),
    "TE": _ReceivingYardsParams(c0=6.99, c1=-2.71, a=2.33, b=0.678),
}
RECEIVING_YARDS_SHIFT = 5.0


def _receiving_params(position: str) -> _ReceivingYardsParams:
    try:
        return RECEIVING_YARDS_PARAMS[position]
    except KeyError:
        raise ValueError(f"no receiving-yards model for position {position!r} (WR, TE)") from None


def receiving_yards_pi0_relation(mu: float, position: str) -> float:
    """P(0 yards) from the overall mean when there's nothing better: expit(c0 + c1 ln mu)."""
    p = _receiving_params(position)
    return float(expit(p.c0 + p.c1 * math.log(mu)))


def receiving_yards(
    mu: float, sd: float, position: str, pi0: float | None = None
) -> HurdleShiftedGamma:
    """Receiving-yards Stat Distribution for a WR or TE with overall mean mu.

    sd is the sd of the continuous nonzero part. pi0 = P(0 yards) defaults to the
    relation expit(c0 + c1 ln mu) (see `receiving_yards_pi0_relation`); the fit
    passes its own. The nonzero part must then have mean
    mu / (1 − pi0); the gamma's continuous mean m is solved so the discretised,
    zero-truncated nonzero mean hits that exactly, making the returned
    distribution's mean exactly mu.

    The location is mu rather than m because m(mu) = mu / (1 − pi0(mu)) is not
    monotone: for WRs it bottoms out near m ≈ 30 at mu ≈ 18, so an m below that
    has no consistent pi0 and an m above it has two.
    """
    _receiving_params(position)
    if mu <= 0 or sd <= 0:
        raise ValueError(f"invalid parameters mu={mu}, sd={sd}")
    if pi0 is None:
        pi0 = receiving_yards_pi0_relation(mu, position)
    if not 0 <= pi0 < 1:
        raise ValueError(f"invalid parameter pi0={pi0}")
    target = mu / (1 - pi0)

    def nonzero_mean_error(m: float) -> float:
        return HurdleShiftedGamma(m, sd, 0.0, RECEIVING_YARDS_SHIFT).mean - target

    # Discretisation and zero-truncation move the mean by well under 1 yd, and
    # d(nonzero mean)/dm ≈ 1, so a Newton-like iteration m ← m − error converges fast.
    m = target
    for _ in range(100):
        err = nonzero_mean_error(m)
        if abs(err) < 1e-11 * target:
            return HurdleShiftedGamma(m, sd, pi0, RECEIVING_YARDS_SHIFT)
        m = max(m - err, m / 2)
    raise FitError(f"nonzero mean did not converge for mu={mu}, sd={sd}")


# Fit settings, research/stat-distributions.md Q3.
FIT_TAU = 0.10  # logit noise of a devigged Rung
FIT_SIGMA_P = 0.15  # prior sd of ln sd around the dispersion relation
FIT_LAMBDA = FIT_TAU**2 / FIT_SIGMA_P**2  # ≈ 0.44, weight of the prior against Σ wᵢ rᵢ²
# Prior sd of logit pi0 around the pi0 relation. Wide on purpose (review finding 2): a
# Ladder starts at 5+ or more and says little about P(0), so the relation is only a
# starting point, and the Main Line must win when they disagree. Sprint 3 replaces
# the relation with P(0 receptions) from the receptions market.
FIT_SIGMA_PI0 = 0.5
FIT_LAMBDA_PI0 = FIT_TAU**2 / FIT_SIGMA_PI0**2  # = 0.04
MAIN_LINE_ONLY_KAPPA = 0.93  # book-implied sd ≈ 0.93 × the nflverse-proxy relation
_SF_CLIP = 1e-12
_MU_RANGE = (1.0, 500.0)  # mu the model allows, yards: the root-find bracket and the fit's bounds
_PI0_MAX = 1 - 1e-6  # keeps 1 − pi0 away from 0 in the fit


def receiving_yards_sd_relation(mu: float, position: str, pi0: float | None = None) -> float:
    """sd of the nonzero part from the dispersion relation: a·m^b, m = mu / (1 − pi0).

    pi0 defaults to the pi0 relation at mu.
    """
    p = _receiving_params(position)
    if pi0 is None:
        pi0 = receiving_yards_pi0_relation(mu, position)
    return p.a * (mu / (1 - pi0)) ** p.b


def _logit_sf(dist: StatDistribution, x: np.ndarray) -> np.ndarray:
    """logit S(x), with S clipped so the logit stays finite."""
    return logit(np.clip(dist.sf(x), _SF_CLIP, 1 - _SF_CLIP))


def _residuals(dist: StatDistribution, x: np.ndarray, target: np.ndarray) -> np.ndarray:
    """The fit residuals logit p − logit S(x)."""
    return target - _logit_sf(dist, x)


def _fit_main_line_only(point: FairPoint, position: str, pi0: float | None = None) -> HurdleShiftedGamma:
    """Root-find mu so S(x) matches the one Fair Probability, sd = κ·relation(mu).

    pi0 is the given one, or its relation at mu when None.
    """
    x, target = np.array([point.threshold]), logit(point.prob)

    def dist(ln_mu: float) -> HurdleShiftedGamma:
        mu = math.exp(ln_mu)
        sd = MAIN_LINE_ONLY_KAPPA * receiving_yards_sd_relation(mu, position, pi0)
        return receiving_yards(mu, sd, position, pi0)

    def error(ln_mu: float) -> float:
        return _logit_sf(dist(ln_mu), x)[0] - target

    lo, hi = math.log(_MU_RANGE[0]), math.log(_MU_RANGE[1])
    if error(lo) * error(hi) > 0:
        raise FitError(
            f"P(Y >= {point.threshold}) = {point.prob} is out of reach for mu in {_MU_RANGE}"
        )
    return dist(optimize.brentq(error, lo, hi, xtol=1e-12))


def fit_receiving_yards(
    points: Sequence[FairPoint], position: str, pi0: float | None = None
) -> HurdleShiftedGamma:
    """Fit a WR/TE receiving-yards Stat Distribution to Fair Probabilities.

    pi0, when given, fixes P(0 yards) instead of fitting it: research Q4 takes it
    from the receptions market, pi0 = P(receptions = 0) = 1 − receptions.sf(1).
    The fit is then over (ln mu, ln sd) only, without the δ term below.

    Three parameters, theta = (ln mu, ln sd, δ) with logit pi0 = logit pi0_rel(mu) + δ,
    by weighted least squares in logit space plus Gaussian priors on ln sd (centred
    on the dispersion relation) and on δ (centred on 0, i.e. on the pi0 relation):

        Σᵢ wᵢ [logit pᵢ − logit S(xᵢ)]² + λ (ln sd − ln sd_rel(mu, pi0))² + λ₀ δ²

    with λ = τ²/σ_p² and λ₀ = τ²/σ_π0². The δ prior is wide, so a Main Line that
    the pi0 relation can't reach (a low-volume player) moves pi0 instead of being
    missed. S(x) = P(Y >= x) of the discretised distribution, which already
    evaluates the continuous part at x − ½ (the 0.5 continuity correction).

    With a single point (a Main Line only), pi0 stays on its relation (or the given
    pi0), sd is fixed at 0.93·sd_rel(mu), and mu is root-found to reproduce the point.
    """
    _receiving_params(position)  # validate before doing any work
    if pi0 is not None and not 0 <= pi0 < 1:
        raise ValueError(f"invalid parameter pi0={pi0}")
    if not points:
        raise FitError("no Fair Probabilities to fit")
    # The 1-point fit is the fallback and the starting point for the full fit. Its
    # anchor is the heaviest point (the Main Line, when present); among equal
    # weights, the one nearest 50%, not an extreme Rung such as "10+" at −2000.
    anchor = max(points, key=lambda p: (p.weight, -abs(p.prob - 0.5)))
    start = _fit_main_line_only(anchor, position, pi0)
    if len(points) == 1:
        return start

    x = np.array([p.threshold for p in points], dtype=float)
    target = logit(np.array([p.prob for p in points]))
    w = np.array([p.weight for p in points], dtype=float)
    ln_mu_range = np.log(_MU_RANGE)
    fixed_pi0 = pi0

    def params(theta: np.ndarray) -> tuple[float, float, float]:
        mu, sd = math.exp(theta[0]), math.exp(theta[1])
        if fixed_pi0 is not None:
            return mu, sd, fixed_pi0
        pi0 = float(expit(logit(receiving_yards_pi0_relation(mu, position)) + theta[2]))
        return mu, sd, pi0

    def pi0_prior(theta: np.ndarray) -> float:
        return 0.0 if fixed_pi0 is not None else FIT_LAMBDA_PI0 * theta[2] ** 2

    def objective(theta: np.ndarray) -> float:
        # Outside the model's range the objective is +inf, which Nelder-Mead steps away from
        # (review finding 5: at tiny mu, pi0 rounds to 1 and the distribution can't be built).
        if not ln_mu_range[0] <= theta[0] <= ln_mu_range[1] or abs(theta[1]) > 20:
            return math.inf
        mu, sd, pi0 = params(theta)
        if pi0 > _PI0_MAX:
            return math.inf
        try:
            dist = receiving_yards(mu, sd, position, pi0)
        except ValueError:  # FitError included: the nonzero mean did not converge
            return math.inf
        resid = _residuals(dist, x, target)
        sd_prior = theta[1] - math.log(receiving_yards_sd_relation(mu, position, pi0))
        return float(np.sum(w * resid**2) + FIT_LAMBDA * sd_prior**2 + pi0_prior(theta))

    # theta = (ln mu, ln sd, δ), or (ln mu, ln sd) when pi0 is given.
    steps = [0.1, 0.1] if fixed_pi0 is not None else [0.1, 0.1, 0.5]
    theta0 = np.array([math.log(start.mean), math.log(start.sd), 0.0][: len(steps)])
    # Explicit steps: the default simplex steps 5% of each coordinate, i.e. ~0 for δ = 0.
    simplex = np.vstack([theta0, theta0 + np.diag(steps)])
    result = optimize.minimize(
        objective,
        theta0,
        method="Nelder-Mead",
        options={"xatol": 1e-7, "fatol": 1e-12, "initial_simplex": simplex, "maxiter": 3000},
    )
    if not math.isfinite(result.fun):
        raise FitError("no fit within the model's range")
    mu, sd, pi0 = params(result.x)
    return receiving_yards(mu, sd, position, pi0)


class Poisson:
    """Poisson(lam) counts: TDs from an anytime-TD price, and the Base Rate stats.

    Integer support 0, 1, 2, ...; sf(x) = P(Y >= x).
    """

    def __init__(self, lam: float):
        if not lam >= 0:
            raise ValueError(f"invalid Poisson rate {lam}")
        self.lam = float(lam)
        self._dist = stats.poisson(self.lam)

    def sf(self, x: np.ndarray) -> np.ndarray:
        x = np.ceil(np.asarray(x, dtype=float))
        return np.where(x <= 0, 1.0, self._dist.sf(x - 1))

    def ppf(self, u: np.ndarray) -> np.ndarray:
        if self.lam == 0:
            return np.zeros_like(np.asarray(u, dtype=float))
        return self._dist.ppf(np.asarray(u, dtype=float))

    @property
    def mean(self) -> float:
        return self.lam


class NegativeBinomial:
    """Receptions: NB with mean mu and size r, so Var = mu + mu²/r (research Q1b, Q4).

    Integer support 0, 1, 2, ...; sf(x) = P(Y >= x). As r → ∞ it tends to Poisson(mu).
    """

    def __init__(self, mu: float, r: float):
        if not (mu > 0 and r > 0):
            raise ValueError(f"invalid negative binomial parameters mu={mu}, r={r}")
        self.mu, self.r = float(mu), float(r)
        self._dist = stats.nbinom(self.r, self.r / (self.r + self.mu))

    def sf(self, x: np.ndarray) -> np.ndarray:
        """P(Y >= x); a fractional x is rounded up (Y >= 4.5 means Y >= 5)."""
        x = np.ceil(np.asarray(x, dtype=float))
        return np.where(x <= 0, 1.0, self._dist.sf(x - 1))

    def ppf(self, u: np.ndarray) -> np.ndarray:
        """Smallest integer k with P(Y <= k) >= u."""
        return self._dist.ppf(np.asarray(u, dtype=float))

    @property
    def mean(self) -> float:
        return self.mu


# Receptions fit. r is fitted only with this many points besides the Main Line (research Q3:
# "fix r from r(μ) unless there are ≥3 Rungs"); with fewer it stays on the relation.
RECEPTIONS_MIN_POINTS_FOR_R = 3
# Prior sd of ln r around the relation. Weak on purpose: r is pinned only by a full Ladder, and
# then the Ladder should win. Var − mu = mu²/r, so 0.5 in ln r is ±65% on the excess variance.
FIT_SIGMA_LN_R = 0.5
FIT_LAMBDA_R = FIT_TAU**2 / FIT_SIGMA_LN_R**2  # = 0.04
_REC_MU_RANGE = (0.01, 40.0)  # mu the model allows, catches
_LN_R_RANGE = (math.log(0.05), math.log(1e4))  # 1e4: Poisson for any practical purpose


def receptions_r_relation(mu: float, position: str) -> float:
    """NB size from the dispersion relation, r = r_a·mu^r_b (params "dispersion")."""
    try:
        rel = params.load("dispersion")["receptions"][position]
    except KeyError:
        raise ValueError(f"no receptions model for position {position!r}") from None
    return rel["r_a"] * mu ** rel["r_b"]


def _receptions_on_relation(mu: float, position: str) -> NegativeBinomial:
    return NegativeBinomial(mu, receptions_r_relation(mu, position))


def _fit_receptions_one_point(point: FairPoint, position: str) -> NegativeBinomial:
    """Root-find mu so P(Y >= threshold) is the one Fair Probability, r = r(mu)."""
    x, target = np.array([point.threshold]), logit(point.prob)

    def error(ln_mu: float) -> float:
        return _logit_sf(_receptions_on_relation(math.exp(ln_mu), position), x)[0] - target

    lo, hi = math.log(_REC_MU_RANGE[0]), math.log(_REC_MU_RANGE[1])
    if error(lo) * error(hi) > 0:
        raise FitError(
            f"P(receptions >= {point.threshold}) = {point.prob} is out of reach for mu in {_REC_MU_RANGE}"
        )
    return _receptions_on_relation(math.exp(optimize.brentq(error, lo, hi, xtol=1e-12)), position)


def fit_receptions(points: Sequence[FairPoint], position: str) -> NegativeBinomial:
    """Fit a receptions Stat Distribution (negative binomial) to Fair Probabilities.

    Weighted least squares in logit space, as for receiving yards:

        Σᵢ wᵢ [logit pᵢ − logit S(xᵢ)]² + λ_r (ln r − ln r_rel(mu))²

    over theta = (ln mu, ln r), λ_r = τ²/σ_r² with σ_r = FIT_SIGMA_LN_R. r is free
    only with at least RECEPTIONS_MIN_POINTS_FOR_R points besides the Main Line (the
    heaviest point, when it has Main Line weight); otherwise r = r_rel(mu) and only
    mu is fitted. With a single point, mu is root-found to reproduce it exactly.
    """
    receptions_r_relation(1.0, position)  # validate before doing any work
    if not points:
        raise FitError("no Fair Probabilities to fit")
    # The anchor is the heaviest point (the Main Line, when present), as in fit_receiving_yards.
    anchor = max(points, key=lambda p: (p.weight, -abs(p.prob - 0.5)))
    start = _fit_receptions_one_point(anchor, position)
    if len(points) == 1:
        return start

    x = np.array([p.threshold for p in points], dtype=float)
    target = logit(np.array([p.prob for p in points]))
    w = np.array([p.weight for p in points], dtype=float)
    ln_mu_range = np.log(_REC_MU_RANGE)
    besides_main = len(points) - (anchor.weight >= MAIN_LINE_WEIGHT)

    if besides_main < RECEPTIONS_MIN_POINTS_FOR_R:

        def objective_mu(ln_mu: float) -> float:
            dist = _receptions_on_relation(math.exp(ln_mu), position)
            return float(np.sum(w * _residuals(dist, x, target) ** 2))

        result = optimize.minimize_scalar(
            objective_mu, bounds=tuple(ln_mu_range), method="bounded", options={"xatol": 1e-10}
        )
        return _receptions_on_relation(math.exp(result.x), position)

    def objective(theta: np.ndarray) -> float:
        in_range = ln_mu_range[0] <= theta[0] <= ln_mu_range[1] and _LN_R_RANGE[0] <= theta[1] <= _LN_R_RANGE[1]
        if not in_range:
            return math.inf
        mu = math.exp(theta[0])
        prior = theta[1] - math.log(receptions_r_relation(mu, position))
        resid = _residuals(NegativeBinomial(mu, math.exp(theta[1])), x, target)
        return float(np.sum(w * resid**2) + FIT_LAMBDA_R * prior**2)

    theta0 = np.array([math.log(start.mu), math.log(start.r)])
    simplex = np.vstack([theta0, theta0 + np.diag([0.1, 0.3])])
    result = optimize.minimize(
        objective,
        theta0,
        method="Nelder-Mead",
        options={"xatol": 1e-8, "fatol": 1e-13, "initial_simplex": simplex, "maxiter": 3000},
    )
    if not math.isfinite(result.fun):
        raise FitError("no receptions fit within the model's range")
    return NegativeBinomial(math.exp(result.x[0]), math.exp(result.x[1]))
