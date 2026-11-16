"""Scoring Rules: presets, Custom Scoring Rules from JSON, and stat line -> fantasy points."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

DEFAULT_RULES = "espn-league"

# The offensive Scoring Stats, i.e. the stat line keys that change fantasy points.
SCORING_STATS = (
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "passing_2pt",
    "rushing_yards",
    "rushing_tds",
    "rushing_2pt",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "receiving_2pt",
    "fumbles_lost",
)


@dataclass(frozen=True)
class ScoringRules:
    """Points per unit of each Scoring Stat.

    `offense` maps every Scoring Stat to its points per unit. `kicker` and `dst`
    are kept as loaded from a Custom Scoring Rules file but are not scored yet.
    `assumptions` lists anything in the rules that was assumed, not confirmed.
    """

    name: str
    offense: Mapping[str, float]
    kicker: dict = field(default_factory=dict)
    dst: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)


def _offense(points_per_unit: Mapping[str, float], source: str) -> Mapping[str, float]:
    """Validate an offense section and fill missing Scoring Stats with 0 points."""
    for stat in points_per_unit:
        if stat not in SCORING_STATS:
            raise ValueError(f"{source}: unknown Scoring Stat {stat!r} in offense")
    return MappingProxyType({stat: points_per_unit.get(stat, 0) for stat in SCORING_STATS})


_STANDARD_OFFENSE = {
    "passing_yards": 0.04,
    "passing_tds": 4,
    "passing_interceptions": -2,
    "passing_2pt": 2,
    "rushing_yards": 0.1,
    "rushing_tds": 6,
    "rushing_2pt": 2,
    "receptions": 0,
    "receiving_yards": 0.1,
    "receiving_tds": 6,
    "receiving_2pt": 2,
    "fumbles_lost": -2,
}


def _preset(name: str, points_per_reception: float) -> ScoringRules:
    offense = {**_STANDARD_OFFENSE, "receptions": points_per_reception}
    return ScoringRules(name=name, offense=_offense(offense, name))


PRESETS = {
    "standard": _preset("standard", 0),
    "half-ppr": _preset("half-ppr", 0.5),
    "ppr": _preset("ppr", 1),
}


def load_rules(name_or_path: str | Path, scoring_dir: Path = Path("scoring")) -> ScoringRules:
    """Look up Scoring Rules by preset name, JSON file path, or `<scoring_dir>/<name>.json`."""
    if name_or_path in PRESETS:
        return PRESETS[name_or_path]
    path = Path(name_or_path)
    if not path.is_file():
        path = Path(scoring_dir) / f"{name_or_path}.json"
    if not path.is_file():
        raise ValueError(
            f"unknown Scoring Rules {str(name_or_path)!r}: not a preset "
            f"({', '.join(PRESETS)}), not a file, and {path} does not exist"
        )
    return _custom_rules(path)


def _custom_rules(path: Path) -> ScoringRules:
    """Read Custom Scoring Rules from a JSON file."""
    data = json.loads(path.read_text())
    return ScoringRules(
        name=data["name"],
        offense=_offense(data["offense"], str(path)),
        kicker=data.get("kicker", {}),
        dst=data.get("dst", {}),
        assumptions=data.get("assumptions", []),
    )


def score(stat_line: Mapping, rules: ScoringRules):
    """Fantasy points for a stat line under the given Scoring Rules.

    Stats missing from the stat line count as 0. Values may be plain numbers or
    numpy arrays of Monte Carlo samples; arrays are scored elementwise.
    """
    return sum(points * stat_line.get(stat, 0) for stat, points in rules.offense.items())
