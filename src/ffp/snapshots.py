"""Snapshots: each live run's raw sportsbook responses and Fair Probabilities, saved to disk.

Sportsbooks expose no past prices (ADR 0001), so these files are our own price
history for later backtesting.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from ffp.fair import FairPoint


class Snapshot:
    """Writes one run's files under <root>/<YYYY-MM-DD>/<HHMMSSZ>/."""

    def __init__(self, run_at: datetime, root: Path = Path("snapshots")) -> None:
        self.run_at = run_at
        self.run_dir = Path(root) / run_at.strftime("%Y-%m-%d") / run_at.strftime("%H%M%SZ")
        self.run_dir.mkdir(parents=True)
        self._fair_points: list[dict] = []

    def save_response(self, book: str, name: str, payload: dict | list | str | bytes) -> Path:
        path = self.run_dir / f"{book}-{name}.json"
        path.write_text(json.dumps(payload, indent=2))
        return path

    def save_fair_points(
        self,
        player: str,
        stat: str,
        points: Sequence[FairPoint],
        *,
        game_id: str | None = None,
    ) -> Path:
        self._fair_points.append(
            {
                "player": player,
                "stat": stat,
                "game_id": game_id,
                "points": [asdict(p) for p in points],
            }
        )
        path = self.run_dir / "fair_points.json"
        path.write_text(json.dumps({"fair_points": self._fair_points}, indent=2))
        return path
