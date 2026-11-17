"""Kickers (S6.3): a kicker's Score Distribution from his team's Implied Team Total.

There is no kicker Prop Market in the fit (PLAN Sprint 6: DraftKings alone lists them, late, and
probably scores "kicking points" without distance); the model runs on the Game Lines, with
parameters from nflverse (params/kicker.json, scripts/build_params.py kicker_params):

1. FG attempts ~ Binomial(18, μ_f/18) and PAT attempts ~ Binomial(12, μ_p/12), each mean a
   log-linear GLM in the Implied Team Total, the expected margin and the season. PATs follow the
   team's TDs; FG attempts barely move with it. The two counts are joined by a Gaussian copula
   (latent ρ ≈ −0.19: a drive ends in one or the other).
2. Each FG's distance from the attempts of the last two seasons (distance has been drifting up).
3. Made with the league's logistic make curve in distance, with a season trend; a blocked kick is
   a miss. Kicker skill does not persist year to year, so every kicker gets the league curve.
4. Each PAT made at the league rate.

Game Lines explain about 3% of a kicker's points (research/kickers-dst.md): his percentiles
barely differ from another kicker's, by design.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from scipy import special, stats

from ffp import params
from ffp.scoring import ScoringRules, score_kicker
from ffp.simulate import DEFAULT_SAMPLES, DEFAULT_SEED, ScoreDistribution


@dataclass(frozen=True)
class KickerSamples:
    """Monte Carlo samples of one kicker's Game: counts, and the distances of the FGs made
    (NaN where there was no made kick), as `score_kicker` reads them."""

    fga: np.ndarray
    fg_made_yards: np.ndarray  # (n, max attempts)
    fg_missed: np.ndarray
    pat_made: np.ndarray
    pat_missed: np.ndarray

    def stat_line(self) -> dict[str, np.ndarray]:
        return {"fg_made_yards": self.fg_made_yards, "fg_missed": self.fg_missed,
                "pat_made": self.pat_made, "pat_missed": self.pat_missed}


def _mean(coef: Mapping[str, float], itt: float, fav_by: float, season_offset: int) -> float:
    return float(np.exp(coef["intercept"] + coef["itt"] * itt + coef["fav_by"] * fav_by + coef["season"] * season_offset))


def attempt_means(itt: float, fav_by: float, season: int, p: Mapping | None = None) -> tuple[float, float]:
    """Expected (FG attempts, PAT attempts) for a team with Implied Team Total `itt`, favoured by
    `fav_by` points (negative: the underdog), in `season`."""
    p = params.load("kicker") if p is None else p
    offset = season - int(p["reference_season"])
    return (_mean(p["fga"]["log_mean"], itt, fav_by, offset), _mean(p["pat_attempts"]["log_mean"], itt, fav_by, offset))


def make_probability(yards: np.ndarray, season: int, p: Mapping | None = None) -> np.ndarray:
    """The league's FG make probability at each distance in `season`."""
    p = params.load("kicker") if p is None else p
    c = p["fg_make_logit"]
    offset = season - int(p["reference_season"])
    return special.expit(c["intercept"] + c["per_yard_over_40"] * (np.asarray(yards, dtype=float) - 40)
                         + c["season"] * offset)


def sample_kicker(itt: float, fav_by: float, season: int, n: int = DEFAULT_SAMPLES, seed: int = DEFAULT_SEED,
                  p: Mapping | None = None) -> KickerSamples:
    """n draws of a kicker's FGs and PATs given his team's Game Lines (see the module docstring)."""
    p = params.load("kicker") if p is None else p
    rng = np.random.default_rng(seed)
    mu_f, mu_p = attempt_means(itt, fav_by, season, p)
    n_f, n_p = int(p["fga"]["trials"]), int(p["pat_attempts"]["trials"])
    rho = float(p["attempts_latent_rho"])
    z1 = rng.standard_normal(n)
    z2 = rho * z1 + np.sqrt(1 - rho * rho) * rng.standard_normal(n)
    fga = stats.binom.ppf(special.ndtr(z1), n_f, min(mu_f / n_f, 1.0)).astype(int)
    pat = stats.binom.ppf(special.ndtr(z2), n_p, min(mu_p / n_p, 1.0)).astype(int)

    dist = p["fg_distance"]
    pmf = np.asarray(dist["pmf"], dtype=float)
    width = max(int(fga.max()), 1)
    yards = rng.choice(np.arange(dist["lo"], dist["lo"] + len(pmf)), size=(n, width), p=pmf / pmf.sum()).astype(float)
    kicked = np.arange(width) < fga[:, None]
    made = kicked & (rng.uniform(size=(n, width)) < make_probability(yards, season, p))
    pat_made = rng.binomial(pat, float(p["pat_make_rate"]))
    return KickerSamples(fga, np.where(made, yards, np.nan), (kicked & ~made).sum(axis=1), pat_made, pat - pat_made)


def kicker_scores(itt: float, fav_by: float, season: int, rules: ScoringRules, n: int = DEFAULT_SAMPLES,
                  seed: int = DEFAULT_SEED) -> tuple[ScoreDistribution, KickerSamples]:
    """The kicker's Score Distribution under `rules`, and the samples behind it."""
    samples = sample_kicker(itt, fav_by, season, n, seed)
    return ScoreDistribution(np.asarray(score_kicker(samples.stat_line(), rules), dtype=float)), samples
