"""Polite cached GET client for Kalshi's public trade API (research only).

Raw responses are cached under data/kalshi/research/ keyed by path+params.
Paces to <= 4 requests/s and logs rate-limit headers it sees.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.elections.kalshi.com/trade-api/v2"
CACHE = Path(__file__).resolve().parents[2] / "data" / "kalshi" / "research"
MIN_GAP = 0.25
_last = 0.0
STATS = {"net": 0, "cached": 0, "headers": {}}


def _key(path: str, params: dict | None) -> Path:
    q = urllib.parse.urlencode(sorted((params or {}).items()))
    h = hashlib.sha1(f"{path}?{q}".encode()).hexdigest()[:16]
    safe = path.strip("/").replace("/", "_")[:80]
    return CACHE / f"{safe}__{h}.json"


def get(path: str, params: dict | None = None, refresh: bool = False) -> dict:
    global _last
    f = _key(path, params)
    if f.exists() and not refresh:
        STATS["cached"] += 1
        return json.loads(f.read_text())
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(5):
        wait = MIN_GAP - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "research-script/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                STATS["net"] += 1
                for k, v in r.headers.items():
                    if "rate" in k.lower() or "limit" in k.lower() or "retry" in k.lower():
                        STATS["headers"][k] = v
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("429; headers:", dict(e.headers), file=sys.stderr)
                time.sleep(2 ** attempt)
                continue
            if e.code in (400, 404):
                return {"_error": e.code, "_body": e.read().decode()[:500]}  # not cached
            raise
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data))
    return data


def paged(path: str, key: str, params: dict, max_pages: int = 200) -> list:
    out, cursor = [], None
    for _ in range(max_pages):
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        d = get(path, p)
        out.extend(d.get(key) or [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out
