#!/usr/bin/env python
"""Record what each free live-stats source says, every minute through one Game (S9.1 Live test).

    python scripts/record_live.py [--event 401873066] [--start 2026-11-20T01:00:00Z]
                                  [--interval 60] [--after-final 30] [--max-minutes 330]
                                  [--vantage home] [--out snapshots/live-spike]

Each poll fetches the ESPN scoreboard, the ESPN summary for the Game, and Sleeper's
weekly stats (research/leaderboard-stats-sources.md). Every body is saved gzipped at
<out>/<source>/<UTC timestamp>.json.gz and every request (or skipped request) gets one
line in <out>/manifest-<vantage>.jsonl: time, URL, HTTP status, latency, bytes, the
game state/period/clock, the play count, cache headers, and a hash of the player stats
in the response. scripts/analyze_live_spike.py reads those manifests.

Polling stops once the scoreboard has shown the Game Final for --after-final minutes,
or after --max-minutes. A 403 or 429 backs that source off for 1, 2, 4, ... polls (up
to 16) and is logged. Without --event the earliest Game not yet Final is used; season
and week come from the scoreboard. The network, clock and sleep are injected so tests
use fakes.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "ffp-live-spike/0.1 (non-commercial fantasy research; one request per source per minute)"
SOURCES = ("espn-scoreboard", "espn-summary", "sleeper-stats")
BLOCKED = (403, 429)
MAX_BACKOFF_POLLS = 16
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
SLEEPER_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

Response = tuple  # (status, body bytes, headers dict)
Fetch = Callable[..., Response]


def source_urls(event_id: str, season: int, week: int) -> dict[str, str]:
    positions = "".join(f"&position[]={p}" for p in SLEEPER_POSITIONS)
    return {
        "espn-scoreboard": f"{ESPN}/scoreboard",
        "espn-summary": f"{ESPN}/summary?event={event_id}",
        "sleeper-stats": f"https://api.sleeper.com/stats/nfl/{season}/{week}?season_type=regular{positions}",
    }


def source_of(url: str) -> str:
    if "sleeper" in url:
        return "sleeper-stats"
    return "espn-summary" if "/summary" in url else "espn-scoreboard"


def stats_hash(obj) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _status_fields(status: dict) -> dict:
    return {"state": status.get("type", {}).get("state"), "period": status.get("period"),
            "clock": status.get("displayClock")}


def _scoreboard(data, event_id) -> dict:
    event = next((e for e in data.get("events", []) if e.get("id") == event_id), None)
    if event is None:
        return {"error": f"event {event_id} not on the scoreboard"}
    comp = event["competitions"][0]
    teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    score = None
    if {"home", "away"} <= teams.keys():
        a, h = teams["away"], teams["home"]
        score = f"{a['team']['abbreviation']} {a.get('score')} - {h['team']['abbreviation']} {h.get('score')}"
    last_play = (comp.get("situation") or {}).get("lastPlay", {}).get("id")
    return {**_status_fields(event.get("status", {})), "score": score, "last_play": last_play,
            "stats_hash": stats_hash(comp.get("leaders", []))}


def _summary(data, _event_id) -> dict:
    drives = data.get("drives", {})
    plays = sum(len(d.get("plays", [])) for d in drives.get("previous", []))
    plays += len((drives.get("current") or {}).get("plays", []))
    status = data.get("header", {}).get("competitions", [{}])[0].get("status", {})
    return {**_status_fields(status), "plays": plays,
            "stats_hash": stats_hash(data.get("boxscore", {}).get("players", []))}


def _sleeper(rows, _event_id) -> dict:
    stats = sorted((str(r.get("player_id")), r.get("stats") or {}) for r in rows)
    modified = [r["last_modified"] for r in rows if r.get("last_modified")]
    return {"rows": len(rows), "last_modified": max(modified, default=None), "stats_hash": stats_hash(stats)}


EXTRACTORS = {"espn-scoreboard": _scoreboard, "espn-summary": _summary, "sleeper-stats": _sleeper}
FIELDS = ("state", "period", "clock", "score", "last_play", "plays", "rows", "last_modified", "stats_hash")


def extract(source: str, body: bytes, event_id: str) -> dict:
    """The game clock, play count and player-stats hash a response shows; never raises."""
    info = dict.fromkeys(FIELDS)
    info["error"] = None
    try:
        info.update(EXTRACTORS[source](json.loads(body), event_id))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
        info["error"] = f"parse: {type(e).__name__}: {e}"[:300]
    return info


def pick_event(scoreboard: dict) -> str:
    """The earliest Game on the scoreboard that is not yet Final."""
    open_games = [e for e in scoreboard.get("events", []) if e.get("status", {}).get("type", {}).get("state") != "post"]
    return min(open_games, key=lambda e: e.get("date", ""))["id"]


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def urllib_fetch(url: str, headers: dict | None = None, timeout: float = 20) -> Response:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Recorder:
    """One vantage point's poller: fetch each source, save the body, append the manifest."""

    def __init__(self, out: Path, event_id: str, season: int, week: int, vantage: str = "home",
                 fetch: Fetch = urllib_fetch, now: Callable[[], datetime] = utc_now):
        self.out, self.event_id, self.vantage = Path(out), event_id, vantage
        self.urls = source_urls(event_id, season, week)
        self.fetch, self.now = fetch, now
        self.penalty = dict.fromkeys(SOURCES, 0)
        self.skip_left = dict.fromkeys(SOURCES, 0)
        self.out.mkdir(parents=True, exist_ok=True)
        self.manifest = self.out / f"manifest-{vantage}.jsonl"

    def poll_once(self) -> list[dict]:
        rows = [self._request(source) for source in SOURCES]
        with self.manifest.open("a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return rows

    def _request(self, source: str) -> dict:
        url = self.urls[source]
        started = self.now()
        row = {"ts": iso(started), "vantage": self.vantage, "source": source, "url": url, "status": None,
               "latency_ms": None, "bytes": None, **dict.fromkeys(FIELDS), "cache_control": None, "age": None,
               "cf_cache_status": None, "retry_after": None, "file": None, "error": None, "skipped": None}
        if self.skip_left[source]:
            self.skip_left[source] -= 1
            row["skipped"] = "backoff"
            return row
        try:
            status, body, headers = self.fetch(url, {"User-Agent": USER_AGENT, "Accept": "application/json"})
        except Exception as e:  # network trouble is data here, not a crash
            row["latency_ms"] = round((self.now() - started).total_seconds() * 1000)
            row["error"] = f"{type(e).__name__}: {e}"[:300]
            log(f"{row['ts']} {source}: {row['error']}")
            return row
        row["latency_ms"] = round((self.now() - started).total_seconds() * 1000)
        row["status"], row["bytes"] = status, len(body)
        h = {k.lower(): v for k, v in (headers or {}).items()}
        row.update(cache_control=h.get("cache-control"), age=h.get("age"),
                   cf_cache_status=h.get("cf-cache-status"), retry_after=h.get("retry-after"))
        path = self.out / source / (started.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json.gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body))
        row["file"] = str(path.relative_to(self.out))
        if status in BLOCKED:
            self.penalty[source] = min(max(1, self.penalty[source] * 2), MAX_BACKOFF_POLLS)
            self.skip_left[source] = self.penalty[source]
            log(f"{row['ts']} {source}: HTTP {status}, backing off {self.penalty[source]} poll(s)"
                + (f" (Retry-After {row['retry_after']})" if row["retry_after"] else ""))
            return row
        self.penalty[source] = 0
        if status == 200:
            row.update(extract(source, body, self.event_id))
        else:
            row["error"] = f"HTTP {status}"
        return row


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run(rec: Recorder, start: datetime, now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] = None, interval: float = 60, after_final_minutes: float = 30,
        max_minutes: float = 330) -> int:
    """Poll from `start` every `interval` s until Final + tail, or max_minutes. Returns the poll count."""
    import time

    sleep = sleep or time.sleep
    if now() < start:
        sleep((start - now()).total_seconds())
    first = max(start, now())
    stop_by = first + timedelta(minutes=max_minutes)
    final_at = None
    polls = 0
    while True:
        due = first + timedelta(seconds=interval * polls)
        if due >= stop_by:
            break
        wait = (due - now()).total_seconds()
        if wait > 0:
            sleep(wait)
        rows = rec.poll_once()
        polls += 1
        game = next((r for r in rows if r["state"]), {})
        log(f"{iso(due)} poll {polls}: " + " ".join(f"{r['source']}={r['status'] or r['skipped'] or 'ERR'}"
                                                    for r in rows)
            + (f" | {game.get('state')} Q{game.get('period')} {game.get('clock')}" if game else ""))
        if final_at is None and any(r["state"] == "post" for r in rows):
            final_at = due
            log(f"{iso(due)} Game is Final; polling {after_final_minutes:g} more minute(s)")
        if final_at is not None and due >= final_at + timedelta(minutes=after_final_minutes):
            break
    return polls


def parse_time(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def main(argv=None, fetch: Fetch = urllib_fetch, now=utc_now, sleep=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--event", help="ESPN event id (default: the earliest Game not yet Final)")
    ap.add_argument("--season", type=int, help="default: from the scoreboard")
    ap.add_argument("--week", type=int, help="NFL Week (default: from the scoreboard)")
    ap.add_argument("--start", type=parse_time, help="UTC start time, e.g. 2026-11-20T01:00:00Z (default: now)")
    ap.add_argument("--interval", type=float, default=60, help="seconds between polls (default 60)")
    ap.add_argument("--after-final", type=float, default=30, help="minutes to keep polling once Final (default 30)")
    ap.add_argument("--max-minutes", type=float, default=330, help="hard stop after this many minutes (default 330)")
    ap.add_argument("--vantage", default="home", help="label for this machine in the manifest (default home)")
    ap.add_argument("--out", type=Path, default=ROOT / "snapshots" / "live-spike")
    args = ap.parse_args(argv)

    event, season, week = args.event, args.season, args.week
    if not (event and season and week):
        status, body, _ = fetch(source_urls("", 0, 0)["espn-scoreboard"],
                                {"User-Agent": USER_AGENT, "Accept": "application/json"})
        if status != 200:
            log(f"scoreboard answered HTTP {status}; pass --event, --season and --week")
            return 1
        sb = json.loads(body)
        event = event or pick_event(sb)
        season = season or sb["season"]["year"]
        week = week or sb["week"]["number"]
    log(f"recording event {event}, season {season} week {week}, vantage {args.vantage} -> {args.out}")
    rec = Recorder(args.out, event, season, week, args.vantage, fetch=fetch, now=now)
    polls = run(rec, args.start or now(), now=now, sleep=sleep, interval=args.interval,
                after_final_minutes=args.after_final, max_minutes=args.max_minutes)
    log(f"done: {polls} poll(s); manifest {rec.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
