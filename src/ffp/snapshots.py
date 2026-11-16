"""Snapshots: each live run's raw sportsbook responses and Fair Probabilities, saved to disk.

Sportsbooks expose no past prices (ADR 0001), so these files are our own price
history for later backtesting.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ffp.fair import FairPoint

_FORMATS = {dict: ("json", ".json"), list: ("json", ".json"), str: ("text", ".txt"), bytes: ("bytes", ".bin")}


class Snapshot:
    """Writes one run's files under <root>/<YYYY-MM-DD>/<HHMMSSZ>/."""

    def __init__(self, run_at: datetime, root: Path = Path("snapshots")) -> None:
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        self.run_at = run_at.astimezone(timezone.utc)
        self.run_id = self.run_at.strftime("%H%M%SZ")
        self.run_dir = Path(root) / self.run_at.strftime("%Y-%m-%d") / self.run_id
        self.run_dir.mkdir(parents=True)
        self._responses: list[dict] = []
        self._fair_points: list[dict] = []

    def save_response(
        self,
        book: str,
        name: str,
        payload: dict | list | str | bytes,
        *,
        url: str | None = None,
        fetched_at: datetime | None = None,
    ) -> Path:
        fmt, ext = _FORMATS[type(payload)]
        path = self.run_dir / f"{len(self._responses) + 1:03d}-{book}-{name}{ext}"
        if fmt == "json":
            path.write_text(json.dumps(payload, indent=2))
        elif fmt == "text":
            path.write_text(payload)
        else:
            path.write_bytes(payload)
        self._responses.append(
            {
                "file": path.name,
                "book": book,
                "name": name,
                "url": url,
                "fetched_at": (fetched_at or self.run_at).isoformat(),
                "format": fmt,
            }
        )
        manifest = {"run_id": self.run_id, "run_at": self.run_at.isoformat(), "responses": self._responses}
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
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
