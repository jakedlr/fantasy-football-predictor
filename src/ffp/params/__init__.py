"""Committed model parameters: dispersion relations, correlation matrices, Base Rates.

The JSON files here are generated from nflverse by `scripts/build_params.py`
(each records its `source`). Code reads them through these loaders, never by path.
"""

import json
from functools import cache
from importlib import resources

import numpy as np

CORRELATION_KINDS = ("within", "pooled")


@cache
def load(name: str) -> dict:
    """One params file, e.g. load("dispersion"), parsed. Cached: treat it as read-only."""
    return json.loads(resources.files(__package__).joinpath(f"{name}.json").read_text())


def correlation(position: str, kind: str | None = None) -> tuple[list[str], np.ndarray]:
    """(Scoring Stats in matrix order, latent correlation matrix) for a position.

    kind is "within" (within-player-season, the default) or "pooled" (S3.6 switch).
    """
    doc = load("correlation")
    kind = kind or doc["default"]
    if kind not in CORRELATION_KINDS:
        raise ValueError(f"unknown correlation kind {kind!r} (known: {', '.join(CORRELATION_KINDS)})")
    try:
        entry = doc[kind][position]
    except KeyError:
        raise ValueError(f"no {kind} correlation matrix for position {position!r}") from None
    return list(entry["stats"]), np.array(entry["matrix"], dtype=float)
