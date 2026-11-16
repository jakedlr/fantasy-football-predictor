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


# Custom Scoring Rules shipped with the package (the default league lives here).
BUNDLED_RULES_DIR = Path(__file__).parent / "rules"


def _is_path(name_or_path: str | Path) -> bool:
    """A Path, or a string with a directory part or a .json suffix; anything else is a bare name."""
    if isinstance(name_or_path, Path):
        return True
    path = Path(name_or_path)
    return path.name != name_or_path or path.suffix == ".json"


def load_rules(name_or_path: str | Path, scoring_dir: Path = Path("scoring")) -> ScoringRules:
    """Look up Scoring Rules by preset name, by JSON file path, or by bare name.

    A path (a Path, or a string with a directory part or a .json suffix) is read as
    given. A bare name is looked up as `<scoring_dir>/<name>.json` (relative to the
    working directory, so a local file overrides a bundled one), then as the bundled
    `ffp/rules/<name>.json`.
    """
    if name_or_path in PRESETS:
        return PRESETS[name_or_path]
    if _is_path(name_or_path):
        candidates = [Path(name_or_path)]
    else:
        candidates = [
            Path(scoring_dir) / f"{name_or_path}.json",
            BUNDLED_RULES_DIR / f"{name_or_path}.json",
        ]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        raise ValueError(
            f"unknown Scoring Rules {str(name_or_path)!r}: not a preset "
            f"({', '.join(PRESETS)}), and none of these exist: "
            + ", ".join(str(c) for c in candidates)
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
