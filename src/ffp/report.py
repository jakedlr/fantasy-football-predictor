"""Report: Score Percentiles per player -> OUTPUT.md and output.json."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

CHANCE_THRESHOLDS = (5, 10, 15, 20, 25, 30)
PERCENTILE_KS = (5, 25, 50, 75, 95)


@dataclass(frozen=True)
class StatDetail:
    """How one Scoring Stat's Stat Distribution came out, and what it was fitted from."""

    mean: float
    p50: float
    books: list[str]
    rungs: int


@dataclass(frozen=True)
class PlayerResult:
    player: str
    team: str
    game: str
    kickoff: str
    position: str
    percentiles: dict[int, float]
    chances: dict[int, float]
    flags: list[str] = field(default_factory=list)
    stats: dict[str, StatDetail] = field(default_factory=dict)


@dataclass(frozen=True)
class SkippedPlayer:
    name: str
    reason: str


def write_markdown(
    results: Sequence[PlayerResult],
    skipped: Sequence[SkippedPlayer],
    rules_name: str,
    generated_at: datetime,
    path: Path,
    assumptions: Sequence[str] = (),
) -> None:
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
        lines += ["Assumptions:", ""] + [f"- {a}" for a in assumptions] + [""]
    lines += [
        "## Score Percentiles",
        "",
        "| Player | Pos | Game | " + " | ".join(f"P{k}" for k in PERCENTILE_KS) + " | Flags |",
        "|---|---|---|" + "---:|" * len(PERCENTILE_KS) + "---|",
    ]
    for r in results:
        cells = [r.player, r.position, r.game]
        cells += [f"{r.percentiles[k]:.1f}" for k in PERCENTILE_KS]
        cells += ["; ".join(r.flags)]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Chance of N+ points",
        "",
        "| Player | " + " | ".join(f"{t}+" for t in CHANCE_THRESHOLDS) + " |",
        "|---|" + "---:|" * len(CHANCE_THRESHOLDS),
    ]
    for r in results:
        cells = [r.player] + [f"{100 * r.chances[t]:.1f}%" for t in CHANCE_THRESHOLDS]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Skipped", ""]
    lines += [f"- {s.name}: {s.reason}" for s in skipped] or ["None."]
    Path(path).write_text("\n".join(lines) + "\n")
