"""Snapshots: each live run's raw sportsbook responses and Fair Probabilities, saved to disk.

Sportsbooks expose no past prices (ADR 0001), so these files are our own price
history for later backtesting. Layout of one run:

    <root>/<YYYY-MM-DD>/<HHMMSSZ>[-n]/
        manifest.json         run id, run time, and one entry per saved response
        fair_points.json      the Fair Probabilities used for each fit, by player and stat
        001-<book>-<name>.json|.txt|.bin   raw responses, as received

Dates and times are UTC. Every file is written atomically (temp file + rename).
"""

import json
import os
import re
from collections.abc import Sequence
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ffp.fair import FairPoint
from ffp.sources.http import redact_url





class Snapshot:
    """One live run's snapshot directory, <root>/<YYYY-MM-DD>/<run id>/.

    run_at is the run's start time, passed in (never read from the clock); it
    must be timezone-aware and is filed in UTC. The run id is HHMMSSZ, with a
    -2, -3, ... suffix when another run already started in the same second.
    """

    def __init__(self, run_at: datetime, root: Path = Path("snapshots")) -> None:
        self.run_at = _utc(run_at)
        self.run_id, self.run_dir = _claim_run_dir(
            Path(root) / self.run_at.strftime("%Y-%m-%d"), self.run_at.strftime("%H%M%SZ")
        )
        self._responses: list[dict] = []
        self._fair_points: dict[tuple[str, str], dict] = {}
        self._timing: dict | None = None
        self._lock = threading.Lock()  # the Books save their responses from their own threads (Sprint 7)

    def save_response(
        self,
        book: str,
        name: str,
        payload: dict | list | str | bytes,
        *,
        url: str | None = None,
        fetched_at: datetime | None = None,
    ) -> Path:
        """Save one raw response and list it in manifest.json; returns the file's path.

        Parsed JSON (dict/list) is pretty-printed; str and bytes are written as
        received. url is stored with its secret query parameters removed.
        fetched_at defaults to the run time.
        """
        with self._lock:
            return self._save_response(book, name, payload, url, fetched_at)

    def _save_response(self, book, name, payload, url, fetched_at) -> Path:
        fmt, ext, data = _encode(payload)
        path = self.run_dir / (
            f"{len(self._responses) + 1:03d}-{_safe_part(book, 'book')}-{_safe_part(name, 'response')}{ext}"
        )
        entry = {
            "file": path.name,
            "book": book,
            "name": name,
            "url": None if url is None else redact_url(url),
            "fetched_at": (self.run_at if fetched_at is None else _utc(fetched_at)).isoformat(),
            "format": fmt,
        }
        responses = [*self._responses, entry]
        _write_atomic(path, data)
        try:
            self._write_manifest(responses)
        except BaseException:
            path.unlink(missing_ok=True)  # never leave a response the manifest doesn't list
            raise
        self._responses = responses
        return path

    def save_fair_points(
        self,
        player: str,
        stat: str,
        points: Sequence[FairPoint],
        *,
        game_id: str | None = None,
        player_id: str | None = None,
    ) -> Path:
        """Record the Fair Probabilities used to fit player's stat in fair_points.json.

        One entry per (player_id or, without one, player name; stat): saving the
        same pair again replaces it. Pass the nflverse gsis_id as player_id, so two
        players who share a name keep separate entries. Returns the path of
        fair_points.json.
        """
        fair_points = dict(self._fair_points)
        fair_points[(player_id or player, stat)] = {
            "player": player,
            "player_id": player_id,
            "stat": stat,
            "game_id": game_id,
            "points": [asdict(p) for p in points],
        }
        path = self._write_index("fair_points.json", "fair_points", list(fair_points.values()))
        self._fair_points = fair_points
        return path

    def save_timing(self, books: dict, seconds: float) -> Path:
        """Record the run's odds timing in manifest.json: {"seconds": the whole fetch, "books": each Book's
        live and cached requests, seconds, and whether it failed or was rate-limited (Http.timing)}."""
        with self._lock:
            self._timing = {"seconds": seconds, "books": books}
            return self._write_manifest(self._responses)

    def _write_manifest(self, responses: list[dict]) -> Path:
        extra = {} if self._timing is None else {"timing": self._timing}
        return self._write_index("manifest.json", "responses", responses, **extra)

    def _write_index(self, filename: str, key: str, entries: list[dict], **extra) -> Path:
        """Atomically (re)write one of the run's JSON index files."""
        path = self.run_dir / filename
        doc = {"run_id": self.run_id, "run_at": self.run_at.isoformat(), key: entries, **extra}
        _write_atomic(path, _json_bytes(doc))
        return path


def _utc(t: datetime) -> datetime:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"datetime must be timezone-aware, got {t!r}")
    return t.astimezone(timezone.utc)


def _claim_run_dir(date_dir: Path, base: str) -> tuple[str, Path]:
    """Create and return a run directory no other run has: base, base-2, base-3, ..."""
    date_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        run_id = base if n == 1 else f"{base}-{n}"
        try:
            (date_dir / run_id).mkdir()  # mkdir is atomic, so concurrent runs can't both win
            return run_id, date_dir / run_id
        except FileExistsError:
            n += 1


def _encode(payload: dict | list | str | bytes) -> tuple[str, str, bytes]:
    """(manifest format, file extension, bytes to write) for a response payload."""
    if isinstance(payload, (dict, list)):
        return "json", ".json", _json_bytes(payload)
    if isinstance(payload, str):
        return "text", ".txt", payload.encode()
    if isinstance(payload, (bytes, bytearray)):
        return "bytes", ".bin", bytes(payload)
    raise TypeError(f"payload must be dict, list, str or bytes, not {type(payload).__name__}")


def _json_bytes(obj: object) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()


def _safe_part(text: str, fallback: str) -> str:
    """text as one safe filename component: no path separators, no leading dots."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("._-")[:80]
    return cleaned or fallback


def _write_atomic(path: Path, data: bytes) -> None:
    """Write data to path via a temp file and rename, so path is never half-written."""
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
