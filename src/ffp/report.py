"""Report: Score Percentiles per player -> OUTPUT.md and output.json."""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from ffp.simulate import DEFAULT_PERCENTILES, ScoreDistribution

PERCENTILE_KS = DEFAULT_PERCENTILES


def chances(dist: ScoreDistribution, thresholds: Sequence[float]) -> dict[float, float]:
    """Chance of N or more points for each threshold N the user asked for (`ffp predict --chance N`)."""
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
    flags: list[str] = field(default_factory=list)  # e.g. "incomplete: missing receptions"
    stats: dict[str, StatDetail] = field(default_factory=dict)  # Scoring Stat -> detail
    chances: dict[float, float] = field(default_factory=dict)  # N -> chance of N or more points, when asked for
    mean: float | None = None  # the Score Distribution's mean (Expected Points) under the run's Scoring Rules
    # S11.2: Expected Points by Scoring Rules name, the same draws scored under each (the run's own included).
    expected: dict[str, float] = field(default_factory=dict)
    # S11.2: each Scoring Stat's mean in the draws (QB/RB/WR/TE: ffp.scoring's offensive stats; K: its
    # counts and FGs made by SLEEPER_FG_BUCKETS; D/ST: its counts and what it allowed). Missing ones are 0.
    stat_means: dict[str, float] = field(default_factory=dict)
    player_id: str | None = None  # the Player's gsis_id ("DST-BAL" for a D/ST)


@dataclass(frozen=True)
class SkippedPlayer:
    """A player from INPUT.md with no Score Distribution, and why."""

    name: str
    reason: str


def _row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _percentile_table(results: Sequence[PlayerResult]) -> list[str]:
    lines = [
        _row(["Player", "Pos", "Game", *(f"P{k}" for k in PERCENTILE_KS), "Mean", "Flags"]),
        "|---|---|---|" + "---:|" * (len(PERCENTILE_KS) + 1) + "---|",
    ]
    for r in results:
        values = [f"{r.percentiles[k]:.1f}" for k in PERCENTILE_KS]
        mean = "" if r.mean is None else f"{r.mean:.1f}"
        lines.append(_row([r.player, r.position, r.game, *values, mean, "; ".join(r.flags)]))
    return lines


def _chance_table(results: Sequence[PlayerResult]) -> list[str]:
    """The chance of N or more points for the thresholds asked for, or nothing when none were."""
    thresholds = list(dict.fromkeys(t for r in results for t in r.chances))
    if not thresholds:
        return []
    lines = [
        "## Chance of at least",
        "",
        _row(["Player", *(f"{t:g}+" for t in thresholds)]),
        "|---|" + "---:|" * len(thresholds),
    ]
    for r in results:
        lines.append(_row([r.player, *(f"{100 * r.chances[t]:.1f}%" if t in r.chances else "" for t in thresholds)]))
    return [*lines, ""]


def write_markdown(
    results: Sequence[PlayerResult],
    skipped: Sequence[SkippedPlayer],
    rules_name: str,
    generated_at: datetime,
    path: Path,
    assumptions: Sequence[str] = (),
) -> None:
    """Write OUTPUT.md: Score Percentiles table, the chance table when thresholds were asked for, skipped players.

    Numbers are shown to 1 decimal. `assumptions` (e.g. ScoringRules.assumptions)
    are listed under the header.
    """
    lines = [
        "# Fantasy point predictions",
        "",
        f"Generated {generated_at.isoformat()} · Scoring Rules: {rules_name}",
        "",
        "Pₖ is a Score Percentile: the player has a k% chance of scoring Pₖ points or fewer. "
        "P5 is the floor, P95 the ceiling. Mean is the Score Distribution's mean, the "
        "Expected Points. Each player's next Game, at the prop prices "
        "current when this was generated.",
        "",
    ]
    if assumptions:
        lines += ["Assumptions:", "", *(f"- {a}" for a in assumptions), ""]
    lines += ["## Score Percentiles", "", *_percentile_table(results), ""]
    lines += _chance_table(results)
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
        **({"mean": r.mean} if r.mean is not None else {}),
        "flags": list(r.flags),
        "stats": {stat: asdict(detail) for stat, detail in r.stats.items()},
        **({"chance_of_at_least": {f"{t:g}": p for t, p in r.chances.items()}} if r.chances else {}),
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
