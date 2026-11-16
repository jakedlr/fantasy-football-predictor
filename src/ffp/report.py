"""Report: Score Percentiles per player -> OUTPUT.md and output.json."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from ffp.simulate import DEFAULT_PERCENTILES, ScoreDistribution

CHANCE_THRESHOLDS = (5, 10, 15, 20, 25, 30)
PERCENTILE_KS = DEFAULT_PERCENTILES


def chances(dist: ScoreDistribution, thresholds: Sequence[int] = CHANCE_THRESHOLDS) -> dict[int, float]:
    """Chance of N+ points for each threshold N."""
    return {t: dist.chance_of_at_least(t) for t in thresholds}


@dataclass(frozen=True)
class StatDetail:
    """One Scoring Stat's Stat Distribution summary and what it was fitted from."""

    mean: float
    p50: float
    books: list[str]  # books whose Prop Markets were used
    rungs: int  # number of Rungs in the fit


@dataclass(frozen=True)
class PlayerResult:
    """One player's Score Distribution summary for their next Game."""

    player: str  # as written in INPUT.md
    team: str
    game: str  # label such as "LAC @ BAL"
    kickoff: str  # ISO 8601 UTC
    position: str
    percentiles: dict[int, float]  # k -> Pₖ
    chances: dict[int, float]  # N -> chance of N+ points
    flags: list[str] = field(default_factory=list)  # e.g. "incomplete: missing receptions"
    stats: dict[str, StatDetail] = field(default_factory=dict)  # Scoring Stat -> detail


@dataclass(frozen=True)
class SkippedPlayer:
    """A player from INPUT.md with no Score Distribution, and why."""

    name: str
    reason: str


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _percentile_table(results: Sequence[PlayerResult]) -> list[str]:
    lines = [
        _row(["Player", "Pos", "Game", *(f"P{k}" for k in PERCENTILE_KS), "Flags"]),
        "|---|---|---|" + "---:|" * len(PERCENTILE_KS) + "---|",
    ]
    for r in results:
        values = [f"{r.percentiles[k]:.1f}" for k in PERCENTILE_KS]
        lines.append(_row([r.player, r.position, r.game, *values, "; ".join(r.flags)]))
    return lines


def _chance_table(results: Sequence[PlayerResult]) -> list[str]:
    lines = [
        _row(["Player", *(f"{t}+" for t in CHANCE_THRESHOLDS)]),
        "|---|" + "---:|" * len(CHANCE_THRESHOLDS),
    ]
    for r in results:
        lines.append(_row([r.player, *(f"{100 * r.chances[t]:.1f}%" for t in CHANCE_THRESHOLDS)]))
    return lines


def write_markdown(
    results: Sequence[PlayerResult],
    skipped: Sequence[SkippedPlayer],
    rules_name: str,
    generated_at: datetime,
    path: Path,
    assumptions: Sequence[str] = (),
) -> None:
    """Write OUTPUT.md: Score Percentiles table, chance of N+ points table, skipped players.

    Numbers are shown to 1 decimal. `assumptions` (e.g. ScoringRules.assumptions)
    are listed under the header.
    """
    lines = [
        "# Fantasy point predictions",
        "",
        f"Generated {generated_at.isoformat()} · Scoring Rules: {rules_name}",
        "",
        "Pₖ is a Score Percentile: the player has a k% chance of scoring Pₖ points or fewer. "
        "P5 is the floor, P95 the ceiling. Each player's next Game, at the prop prices "
        "current when this was generated.",
        "",
    ]
    if assumptions:
        lines += ["Assumptions:", "", *(f"- {a}" for a in assumptions), ""]
    lines += ["## Score Percentiles", "", *_percentile_table(results), ""]
    lines += ["## Chance of N+ points", "", *_chance_table(results), ""]
    lines += ["## Skipped", ""]
    lines += [f"- {s.name}: {s.reason}" for s in skipped] or ["None."]
    Path(path).write_text("\n".join(lines) + "\n")


def _player_json(r: PlayerResult) -> dict:
    return {
        "player": r.player,
        "team": r.team,
        "game": r.game,
        "kickoff": r.kickoff,
        "position": r.position,
        "percentiles": {f"P{k}": v for k, v in r.percentiles.items()},
        "chance_of_at_least": {str(t): p for t, p in r.chances.items()},
        "flags": list(r.flags),
        "stats": {stat: asdict(detail) for stat, detail in r.stats.items()},
    }


def write_json(
    results: Sequence[PlayerResult],
    skipped: Sequence[SkippedPlayer],
    rules_name: str,
    generated_at: datetime,
    path: Path,
    assumptions: Sequence[str] = (),
) -> None:
    """Write output.json: everything in OUTPUT.md at full precision, plus per-stat detail."""
    data = {
        "generated_at": generated_at.isoformat(),
        "scoring_rules": rules_name,
        "assumptions": list(assumptions),
        "players": [_player_json(r) for r in results],
        "skipped": [{"player": s.name, "reason": s.reason} for s in skipped],
    }
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
