"""Gaussian copula: correlated uniforms for joint sampling of Stat Distributions.

z ~ N(0, R) by Cholesky, u = Φ(z). Each stat's u then goes through its own
Stat Distribution's ppf (step-CDF inversion for counts), so the margins are
untouched and the dependence comes from R alone. R is the *latent* correlation
(research/nflverse-data.md 3c): with discrete margins the observed Spearman of
the samples is intentionally smaller than R.
"""

from collections.abc import Collection, Sequence

import numpy as np
from scipy.special import ndtr

_TOL = 1e-8
# Φ(z) rounds to exactly 0 or 1 beyond |z| ≈ 8.3 (and 38 for 0); ppf(1) can be inf.
_U_MIN = np.finfo(float).tiny
_U_MAX = 1 - np.finfo(float).epsneg


def validate(stats: Sequence[str], corr: np.ndarray) -> np.ndarray:
    """corr as a float array, or a ValueError saying what's wrong with it."""
    corr = np.asarray(corr, dtype=float)
    if corr.ndim != 2 or corr.shape[0] != corr.shape[1]:
        raise ValueError(f"correlation matrix must be square, got shape {corr.shape}")
    if len(stats) != corr.shape[0]:
        raise ValueError(f"{len(stats)} stats for a {corr.shape[0]}x{corr.shape[0]} correlation matrix")
    if len(set(stats)) != len(stats):
        raise ValueError(f"duplicate stats in {list(stats)}")
    if not np.all(np.isfinite(corr)):
        raise ValueError("correlation matrix must be finite")
    if not np.allclose(corr, corr.T, atol=_TOL, rtol=0):
        raise ValueError("correlation matrix must be symmetric")
    if not np.allclose(np.diag(corr), 1.0, atol=_TOL, rtol=0):
        raise ValueError(f"correlation matrix must have a unit diagonal, got {np.diag(corr)}")
    return corr


def _cholesky(corr: np.ndarray) -> np.ndarray:
    try:
        return np.linalg.cholesky(corr)
    except np.linalg.LinAlgError:
        min_eig = float(np.linalg.eigvalsh(corr).min())
        raise ValueError(
            f"correlation matrix must be positive definite (min eigenvalue {min_eig:.3g})"
        ) from None


def correlated_uniforms(
    stats: Sequence[str], corr: np.ndarray, n: int, rng: np.random.Generator
) -> dict[str, np.ndarray]:
    """n draws of u = Φ(z), z ~ N(0, corr), one array per stat in `stats` (matrix order).

    Every u lies strictly inside (0, 1).
    """
    return correlated_uniforms_unchecked(stats, validate(stats, corr), n, rng)


def correlated_uniforms_unchecked(stats: Sequence[str], corr: np.ndarray, n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    """correlated_uniforms for a corr already through `validate` (or a sub-matrix of one)."""
    lower = _cholesky(corr)
    z = rng.standard_normal((n, len(stats))) @ lower.T
    u = np.clip(ndtr(z), _U_MIN, _U_MAX)
    return {stat: u[:, i] for i, stat in enumerate(stats)}


def submatrix(
    stats_all: Sequence[str], corr: np.ndarray, wanted: Collection[str]
) -> tuple[list[str], np.ndarray]:
    """The stats of `stats_all` that are in `wanted` (in matrix order) and their sub-matrix.

    A wanted stat outside `stats_all` is left out: it has no correlation and is
    sampled independently.
    """
    idx = [i for i, stat in enumerate(stats_all) if stat in wanted]
    corr = np.asarray(corr, dtype=float)
    return [stats_all[i] for i in idx], corr[np.ix_(idx, idx)]
