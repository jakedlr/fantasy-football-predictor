"""Scoring Rules: presets, Custom Scoring Rules from JSON, and stat line -> points."""

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_RULES = "espn-league"

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
    name: str
    offense: dict = field(default_factory=dict)
    kicker: dict = field(default_factory=dict)
    dst: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)


_STANDARD = ScoringRules(
    name="standard",
    offense={
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
    },
)


PRESETS = {
    "standard": _STANDARD,
    "half-ppr": ScoringRules(name="half-ppr", offense={**_STANDARD.offense, "receptions": 0.5}),
    "ppr": ScoringRules(name="ppr", offense={**_STANDARD.offense, "receptions": 1}),
}


def load_rules(name_or_path, scoring_dir=Path("scoring")):
    if name_or_path in PRESETS:
        return PRESETS[name_or_path]
    path = Path(name_or_path)
    if not path.is_file():
        path = Path(scoring_dir) / f"{name_or_path}.json"
    data = json.loads(path.read_text())
    for stat in data["offense"]:
        if stat not in SCORING_STATS:
            raise ValueError(f"{path}: unknown Scoring Stat {stat!r} in offense")
    offense = {stat: data["offense"].get(stat, 0) for stat in SCORING_STATS}
    return ScoringRules(
        name=data["name"],
        offense=offense,
        kicker=data.get("kicker", {}),
        dst=data.get("dst", {}),
        assumptions=data.get("assumptions", []),
    )


def score(stat_line, rules):
    return sum(points * stat_line.get(stat, 0) for stat, points in rules.offense.items())
