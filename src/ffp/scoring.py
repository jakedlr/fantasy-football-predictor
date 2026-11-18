"""Scoring Rules: presets, Custom Scoring Rules from JSON, and stat line -> fantasy points.

Three kinds of stat line, each with its section of the rules: a QB/RB/WR/TE's (`offense`,
`score`), a kicker's (`kicker`, `score_kicker`) and a D/ST's (`dst`, `score_dst`).
"""

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np

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


# A kicker's per-unit Scoring Stats; FGs made score by distance (`fg_made_by_distance` tiers).
KICKER_STATS = ("pat_made", "pat_missed", "fg_missed")
# A D/ST's per-unit Scoring Stats. Its Points Allowed and Yards Allowed score by tier.
DST_STATS = (
    "sacks",
    "interceptions",
    "fumble_recoveries",
    "forced_fumbles",
    "safeties",
    "blocked_kicks",
    "interception_return_tds",
    "fumble_return_tds",
    "kickoff_return_tds",
    "punt_return_tds",
    "blocked_kick_return_tds",
)
# What a league may leave out of Points Allowed: the points (TD and try) of touchdowns the
# opponent's defense scored (INT and fumble returns), or its returners scored (kickoff, punt
# and blocked-kick returns). ESPN and Sleeper leave out the first (PLAN Sprint 6).
POINTS_ALLOWED_EXCLUSIONS = ("opponent_defensive_tds", "opponent_return_tds")
_TIERS = ("points_allowed_tiers", "yards_allowed_tiers")


@dataclass(frozen=True)
class ScoringRules:
    """Points per unit of each Scoring Stat.

    `offense` maps every Scoring Stat to its points per unit. `kicker` and `dst` hold the
    kicker's and D/ST's rules (see `score_kicker`, `score_dst`); empty when the rules
    don't score that position. `assumptions` lists anything in the rules that was
    assumed, not confirmed.
    """

    name: str
    offense: Mapping[str, float]
    kicker: dict = field(default_factory=dict)
    dst: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)

    def __reduce__(self):
        # Pickled for prediction workers (Sprint 8). A mappingproxy can't be pickled, so each read-only
        # field goes as a dict and comes back read-only; every field is carried, however many there are.
        state = {f.name: getattr(self, f.name) for f in fields(self)}
        proxies = [name for name, value in state.items() if isinstance(value, MappingProxyType)]
        return _rules, ({name: dict(v) if name in proxies else v for name, v in state.items()}, proxies)


def _rules(state: dict, proxies: list) -> "ScoringRules":
    return ScoringRules(**{name: MappingProxyType(v) if name in proxies else v for name, v in state.items()})


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


def _tiers(*rows: tuple[int, int | None, float]) -> list[dict]:
    return [{"min": lo, "max": hi, "points": pts} for lo, hi, pts in rows]


# ESPN's default kicker and D/ST scoring (the standard, half-PPR and PPR presets).
_ESPN_KICKER = {
    "pat_made": 1,
    "fg_missed": -1,
    "fg_made_by_distance": _tiers((0, 39, 3), (40, 49, 4), (50, None, 5)),
}
_ESPN_DST = {
    "sacks": 1, "interceptions": 2, "fumble_recoveries": 2, "safeties": 2, "blocked_kicks": 2,
    "interception_return_tds": 6, "fumble_return_tds": 6, "kickoff_return_tds": 6, "punt_return_tds": 6,
    "blocked_kick_return_tds": 6,
    "points_allowed_excludes": ["opponent_defensive_tds"],
    "points_allowed_tiers": _tiers((0, 0, 5), (1, 6, 4), (7, 13, 3), (14, 17, 1), (18, 27, 0), (28, 34, -1),
                                   (35, 45, -3), (46, None, -5)),
    "yards_allowed_tiers": _tiers((0, 99, 5), (100, 199, 3), (200, 299, 2), (300, 349, 0), (350, 399, -1),
                                  (400, 449, -3), (450, 499, -5), (500, 549, -6), (550, None, -7)),
}
# Sleeper's defaults, which Kalshi's fantasy-points markets settle on (research/kalshi-fantasy-points.md;
# forced fumbles and the points-allowed definition checked on 298 settled D/ST markets).
_SLEEPER_KICKER = {
    "pat_made": 1,
    "pat_missed": -1,
    "fg_missed": -1,
    "fg_made_by_distance": _tiers((0, 39, 3), (40, 49, 4), (50, 59, 5), (60, None, 6)),
}
_SLEEPER_DST = {
    "sacks": 1, "interceptions": 2, "fumble_recoveries": 2, "forced_fumbles": 1, "safeties": 2, "blocked_kicks": 2,
    "interception_return_tds": 6, "fumble_return_tds": 6, "kickoff_return_tds": 6, "punt_return_tds": 6,
    "blocked_kick_return_tds": 6,
    "points_allowed_excludes": ["opponent_defensive_tds"],
    "points_allowed_tiers": _tiers((0, 0, 10), (1, 6, 7), (7, 13, 4), (14, 20, 1), (21, 27, 0), (28, 34, -1),
                                   (35, None, -4)),
}
_SLEEPER_ASSUMPTIONS = [
    "D/ST Points Allowed leaves out the opponent's INT and fumble return TDs (it matched 295 of 298 settled "
    "Kalshi D/ST markets); Yards Allowed is not scored.",
]
_ESPN_ASSUMPTIONS = [
    "Kicker and D/ST rules are ESPN's defaults. Points Allowed leaves out the opponent's INT and fumble return "
    "TDs and counts its kick and punt return TDs; Yards Allowed is net of sack yards (secondary sources, not "
    "ESPN's own help page).",
]


def _preset(name: str, kicker: dict, dst: dict, assumptions: list, **changes: float) -> ScoringRules:
    """Standard scoring with `changes` (points per unit by Scoring Stat) applied."""
    return ScoringRules(name=name, offense=_offense({**_STANDARD_OFFENSE, **changes}, name),
                        kicker=_kicker(kicker, name), dst=_dst(dst, name), assumptions=list(assumptions))


def _kicker(section: dict, source: str) -> dict:
    """Validate a kicker section: known keys, FG tiers well formed."""
    for key in section:
        if key not in (*KICKER_STATS, "fg_made_by_distance"):
            raise ValueError(f"{source}: unknown kicker rule {key!r}")
    _check_tiers(section.get("fg_made_by_distance", []), f"{source}: kicker fg_made_by_distance")
    return section


def _dst(section: dict, source: str) -> dict:
    """Validate a D/ST section: known keys and exclusions, tiers well formed."""
    for key in section:
        if key not in (*DST_STATS, *_TIERS, "points_allowed_excludes"):
            raise ValueError(f"{source}: unknown dst rule {key!r}")
    for what in section.get("points_allowed_excludes", []):
        if what not in POINTS_ALLOWED_EXCLUSIONS:
            raise ValueError(f"{source}: unknown points_allowed_excludes {what!r} "
                             f"(known: {', '.join(POINTS_ALLOWED_EXCLUSIONS)})")
    for key in _TIERS:
        _check_tiers(section.get(key, []), f"{source}: dst {key}")
    return section


def _check_tiers(tiers: list, source: str) -> None:
    """Whole-number tiers {"min", "max", "points"} (max inclusive), each starting one past the
    previous tier's max: no gaps, no overlaps. Only the last tier may have no max (open-ended)."""
    for i, tier in enumerate(tiers):
        if set(tier) != {"min", "max", "points"}:
            raise ValueError(f"{source}: a tier needs exactly min, max and points: {tier}")
        if tier["max"] is None and i != len(tiers) - 1:
            raise ValueError(f"{source}: only the last tier may have no max")
        if tier["max"] is not None and tier["max"] < tier["min"]:
            raise ValueError(f"{source}: a tier's max is below its min: {tier}")
        if i and tier["min"] != tiers[i - 1]["max"] + 1:
            prev = tiers[i - 1]
            raise ValueError(f"{source}: tiers must follow on with no gap or overlap: {prev['min']}-{prev['max']} "
                             f"then {tier['min']}-{tier['max']} (expected the next tier to start at {prev['max'] + 1})")


PRESETS = {
    "standard": _preset("standard", _ESPN_KICKER, _ESPN_DST, _ESPN_ASSUMPTIONS),
    "half-ppr": _preset("half-ppr", _ESPN_KICKER, _ESPN_DST, _ESPN_ASSUMPTIONS, receptions=0.5),
    "ppr": _preset("ppr", _ESPN_KICKER, _ESPN_DST, _ESPN_ASSUMPTIONS, receptions=1),
    # Sleeper's default full PPR, which Kalshi's fantasy-points markets settle on
    # (research/kalshi-fantasy-points.md): an interception costs -1, not -2.
    "sleeper": _preset("sleeper", _SLEEPER_KICKER, _SLEEPER_DST, _SLEEPER_ASSUMPTIONS,
                       receptions=1, passing_interceptions=-1),
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
        kicker=_kicker(data.get("kicker", {}), str(path)),
        dst=_dst(data.get("dst", {}), str(path)),
        assumptions=data.get("assumptions", []),
    )


def score(stat_line: Mapping, rules: ScoringRules):
    """Fantasy points for a stat line under the given Scoring Rules.

    Stats missing from the stat line count as 0. Values may be plain numbers or
    numpy arrays of Monte Carlo samples; arrays are scored elementwise.
    """
    return sum(points * stat_line.get(stat, 0) for stat, points in rules.offense.items())


def _tier_points(values, tiers: list) -> np.ndarray:
    """Each value's tier points. Tiers follow on (_check_tiers), so the tier is the last one whose
    min the value reaches; a value below the first tier gets the first tier's points (negative
    Yards Allowed), one above a closed last tier gets none."""
    values = np.asarray(values, dtype=float)
    if not tiers:
        return np.zeros_like(values)
    mins = np.array([t["min"] for t in tiers], dtype=float)
    points = np.array([t["points"] for t in tiers], dtype=float)
    out = points[np.maximum(np.searchsorted(mins, values, side="right") - 1, 0)]
    top = tiers[-1]["max"]
    return out if top is None else np.where(values > top, 0.0, out)


def score_kicker(stat_line: Mapping, rules: ScoringRules):
    """A kicker's fantasy points: `pat_made`, `pat_missed` and `fg_missed` counts, and
    `fg_made_yards`, the distances of the FGs made (one row per sample, NaN where there was
    no kick: shape (n, attempts)). Values may be numbers or numpy arrays of samples."""
    k = rules.kicker
    total = sum(k.get(stat, 0) * np.asarray(stat_line.get(stat, 0), dtype=float) for stat in KICKER_STATS)
    yards = np.asarray(stat_line.get("fg_made_yards", []), dtype=float)
    points = _tier_points(np.nan_to_num(yards, nan=0.0), k.get("fg_made_by_distance", []))
    made = np.where(np.isnan(yards), 0.0, points).sum(axis=-1) if yards.size else 0.0
    total = total + made  # a 1-d fg_made_yards is one sample's kicks
    return total if np.ndim(total) else float(total)


def points_allowed(stat_line: Mapping, rules: ScoringRules):
    """The D/ST's Points Allowed under the rules: `points_allowed_all` less the opponent's
    defensive and/or return TD points the rules leave out."""
    pa = np.asarray(stat_line.get("points_allowed_all", 0), dtype=float)
    for what in rules.dst.get("points_allowed_excludes", []):
        pa = pa - np.asarray(stat_line.get(f"{what}_points", 0), dtype=float)
    return pa


def score_dst(stat_line: Mapping, rules: ScoringRules):
    """A D/ST's fantasy points: the DST_STATS counts, plus the Points Allowed tier (`points_allowed`)
    and the Yards Allowed tier (`yards_allowed`). Values may be numbers or numpy arrays of samples."""
    d = rules.dst
    total = sum(d.get(stat, 0) * np.asarray(stat_line.get(stat, 0), dtype=float) for stat in DST_STATS)
    total = total + _tier_points(points_allowed(stat_line, rules), d.get("points_allowed_tiers", []))
    if d.get("yards_allowed_tiers"):
        total = total + _tier_points(stat_line.get("yards_allowed", 0), d["yards_allowed_tiers"])
    return total if np.ndim(total) else float(total)
