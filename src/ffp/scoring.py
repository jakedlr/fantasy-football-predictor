"""Scoring Rules: presets, Custom Scoring Rules from JSON, and stat line -> points."""

from dataclasses import dataclass, field

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


def load_rules(name_or_path):
    return PRESETS[name_or_path]


def score(stat_line, rules):
    return sum(points * stat_line.get(stat, 0) for stat, points in rules.offense.items())
