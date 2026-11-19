#!/usr/bin/env python
"""Summarise the S9.1 Live test manifests: blocking and staleness per source and vantage point.

    python scripts/analyze_live_spike.py [snapshots/live-spike | manifest-home.jsonl manifest-worker.jsonl ...]

Reads the JSONL manifests written by scripts/record_live.py (vantage "home") and the
Worker's /dump (vantage "worker"); a directory argument means every manifest-*.jsonl
in it. For each (source, vantage point) it reports:

- HTTP status counts (plus network errors and polls skipped while backing off) and
  median latency;
- staleness: "game progress" is the first sighting, from any vantage, of a new
  (period, clock) on the ESPN scoreboard while the Game is Live, or of a new play
  count in the ESPN summary. For each progress time, the lag is the minutes until that
  stream's player-stats hash next changed. Not every play changes a stat (penalties,
  kneels), so the lag is an upper bound; the median is the number to read.
- settle: minutes after the Game went Final until the stream's last stats change.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ORDER = ("espn-scoreboard", "espn-summary", "sleeper-stats")


def parse_ts(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def with_times(rows: list[dict]) -> list[dict]:
    return [{**r, "t": parse_ts(r["ts"])} for r in rows]


def load(paths) -> list[dict]:
    rows = []
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return with_times(rows)


def ok(r: dict) -> bool:
    return r.get("status") == 200 and not r.get("error")


def status_counts(rows: list[dict]) -> dict[tuple[str, str], Counter]:
    counts: dict[tuple[str, str], Counter] = {}
    for r in rows:
        key = "skipped" if r.get("skipped") else "error" if r.get("status") is None else str(r["status"])
        counts.setdefault((r["source"], r["vantage"]), Counter())[key] += 1
    return counts


def progress_times(rows: list[dict]) -> list[datetime]:
    """First sighting of each new Live (period, clock) on the scoreboard or play count in the summary."""
    observations = []
    for r in rows:
        if not ok(r):
            continue
        if r["source"] == "espn-scoreboard" and r.get("state") == "in" and r.get("clock") is not None:
            observations.append((r["t"], "clock", (r.get("period"), r["clock"])))
        elif r["source"] == "espn-summary" and r.get("state") != "pre" and r.get("plays") is not None:
            observations.append((r["t"], "plays", r["plays"]))
    seen: dict[str, set] = {}
    times = set()
    for t, kind, value in sorted(observations, key=lambda o: o[0]):
        values = seen.setdefault(kind, set())
        if values and value not in values:
            times.add(t)
        values.add(value)
    return sorted(times)


def hash_changes(rows: list[dict], source: str, vantage: str) -> list[datetime]:
    stream = sorted((r for r in rows if r["source"] == source and r["vantage"] == vantage
                     and ok(r) and r.get("stats_hash")), key=lambda r: r["t"])
    return [b["t"] for a, b in zip(stream, stream[1:]) if a["stats_hash"] != b["stats_hash"]]


def lags(progress: list[datetime], changes: list[datetime], tolerance_s: float = 30) -> tuple[list[float], int]:
    """Minutes from each progress time to the next change (a change up to tolerance_s earlier counts as 0)."""
    out, unresolved = [], 0
    for p in progress:
        nxt = next((c for c in changes if c >= p - timedelta(seconds=tolerance_s)), None)
        if nxt is None:
            unresolved += 1
        else:
            out.append(max(0.0, (nxt - p).total_seconds() / 60))
    return out, unresolved


def final_time(rows: list[dict]) -> datetime | None:
    finals = [r["t"] for r in rows if ok(r) and r["source"] in ("espn-scoreboard", "espn-summary")
              and r.get("state") == "post"]
    return min(finals, default=None)


def settle_minutes(changes: list[datetime], final: datetime | None) -> float | None:
    if final is None or not changes:
        return None
    return max(0.0, (changes[-1] - final).total_seconds() / 60)


def p90(xs: list[float]) -> float:
    s = sorted(xs)
    return s[max(0, -(-len(s) * 9 // 10) - 1)]


def summarise(rows: list[dict]) -> dict[tuple[str, str], dict]:
    progress = progress_times(rows)
    final = final_time(rows)
    out = {}
    for key, statuses in status_counts(rows).items():
        source, vantage = key
        latencies = [r["latency_ms"] for r in rows if (r["source"], r["vantage"]) == key and ok(r)
                     and r.get("latency_ms") is not None]
        changes = hash_changes(rows, source, vantage)
        lag, unresolved = lags(progress, changes)
        out[key] = {
            "statuses": dict(statuses), "requests": sum(statuses.values()),
            "latency_median_ms": statistics.median(latencies) if latencies else None,
            "hash_changes": len(changes), "progress": len(progress), "unresolved": unresolved,
            "lag_median": statistics.median(lag) if lag else None,
            "lag_p90": p90(lag) if lag else None, "lag_max": max(lag) if lag else None,
            "settle_after_final": settle_minutes(changes, final),
        }
    return out


def _fmt(x, unit=""):
    return "-" if x is None else f"{x:.1f}{unit}" if isinstance(x, float) else f"{x}{unit}"


def report(rows: list[dict]) -> str:
    summary = summarise(rows)
    final = final_time(rows)
    start = min((r["t"] for r in rows), default=None)
    end = max((r["t"] for r in rows), default=None)
    lines = [f"Records: {len(rows)} from {start and start.isoformat()} to {end and end.isoformat()}",
             f"Game progress events: {len(progress_times(rows))}; Final first seen: "
             f"{final.isoformat() if final else 'never'}", ""]
    header = ("source", "vantage", "requests", "statuses", "latency", "changes",
              "lag med", "lag p90", "lag max", "no change", "settle")
    table = [header]
    order = {s: i for i, s in enumerate(SOURCE_ORDER)}
    for (source, vantage), s in sorted(summary.items(), key=lambda kv: (order.get(kv[0][0], 99), kv[0][1])):
        statuses = ", ".join(f"{k}: {v}" for k, v in sorted(s["statuses"].items()))
        table.append((source, vantage, str(s["requests"]), statuses, _fmt(s["latency_median_ms"], " ms"),
                      str(s["hash_changes"]), _fmt(s["lag_median"], " min"), _fmt(s["lag_p90"], " min"),
                      _fmt(s["lag_max"], " min"), str(s["unresolved"]), _fmt(s["settle_after_final"], " min")))
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    for row in table:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    lines += ["", "lag: minutes from game progress (new clock or play count) to the stream's next stats change;",
              "no change: progress events with no later stats change; settle: last change minus Final."]
    return "\n".join(lines)


def manifest_paths(args: list[str]) -> list[Path]:
    paths = []
    for a in args:
        p = Path(a)
        paths += sorted(p.glob("manifest-*.jsonl")) if p.is_dir() else [p]
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("manifests", nargs="*", default=[str(ROOT / "snapshots" / "live-spike")],
                    help="manifest JSONL files or directories (default snapshots/live-spike)")
    args = ap.parse_args(argv)
    paths = manifest_paths(args.manifests)
    if not paths:
        print("no manifests found", file=sys.stderr)
        return 1
    print(report(load(paths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
