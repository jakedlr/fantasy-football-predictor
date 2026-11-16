"""Snapshots: each live run's raw sportsbook responses and Fair Probabilities, saved to disk.

Sportsbooks expose no past prices (ADR 0001), so these files are our own price
history for later backtesting.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from ffp.fair import FairPoint

_FORMATS = {dict: ("json", ".json"), list: ("json", ".json"), str: ("text", ".txt"), bytes: ("bytes", ".bin")}

# Query parameters that carry app keys or credentials (e.g. FanDuel's _ak, PLAN S2.1).
SECRET_QUERY_PARAMS = frozenset({"_ak", "ak", "apikey", "api_key", "key", "token"})


def redact_url(url: str) -> str:
    """url without its secret query parameters; everything else is kept as written."""
    parts = urlsplit(url)
    kept = [
        pair
        for pair in parts.query.split("&")
        if pair and unquote_plus(pair.partition("=")[0]).lower() not in SECRET_QUERY_PARAMS
    ]
    return urlunsplit(parts._replace(query="&".join(kept)))


def _safe_part(text: str, fallback: str) -> str:
    """text as one safe filename component: no separators, no leading dots."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")[:80]
    return cleaned or fallback


class Snapshot:
    """Writes one run's files under <root>/<YYYY-MM-DD>/<HHMMSSZ>/."""

    def __init__(self, run_at: datetime, root: Path = Path("snapshots")) -> None:
        if run_at.tzinfo is None or run_at.utcoffset() is None:
            raise ValueError("run_at must be timezone-aware")
        self.run_at = run_at.astimezone(timezone.utc)
        date_dir = Path(root) / self.run_at.strftime("%Y-%m-%d")
        date_dir.mkdir(parents=True, exist_ok=True)
        base = self.run_at.strftime("%H%M%SZ")
        n = 1
        while True:
            self.run_id = base if n == 1 else f"{base}-{n}"
            self.run_dir = date_dir / self.run_id
            try:
                self.run_dir.mkdir()  # atomic claim: another run may start in the same second
                break
            except FileExistsError:
                n += 1
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
        path = self.run_dir / (
            f"{len(self._responses) + 1:03d}-"
            f"{_safe_part(book, 'book')}-{_safe_part(name, 'response')}{ext}"
        )
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
                "url": None if url is None else redact_url(url),
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
