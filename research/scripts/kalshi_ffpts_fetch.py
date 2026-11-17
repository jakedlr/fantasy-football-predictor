"""Fetch every KXNFLFFPTS / KXNFLFFPTSLADDER market (live + historical endpoints).

Raw pages are cached under data/kalshi/research/ffpts_pages/ (git-ignored); a
re-run reads the cache. Writes data/kalshi/research/ffpts_markets.json with the
de-duplicated market list.

Usage: .venv/bin/python research/scripts/kalshi_ffpts_fetch.py
"""

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://api.elections.kalshi.com/trade-api/v2"
OUT = Path("data/kalshi/research")
PAGES = OUT / "ffpts_pages"
SERIES = ("KXNFLFFPTS", "KXNFLFFPTSLADDER")


def get(path: str, params: dict) -> dict:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    cache = PAGES / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    time.sleep(0.25)  # <= 4 requests/s
    req = urllib.request.Request(url, headers={"User-Agent": "research-script"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read())
    cache.write_text(json.dumps({"url": url, **body}))
    return body


def all_markets(path: str, series: str) -> list[dict]:
    markets, cursor = [], ""
    while True:
        params = {"series_ticker": series, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = get(path, params)
        markets += page.get("markets", [])
        cursor = page.get("cursor") or ""
        if not cursor or not page.get("markets"):
            return markets


def main() -> None:
    PAGES.mkdir(parents=True, exist_ok=True)
    seen = {}
    for series in SERIES:
        for path in ("/markets", "/historical/markets"):
            ms = all_markets(path, series)
            print(f"{series} {path}: {len(ms)}")
            for m in ms:
                m["_source"] = path
                seen.setdefault(m["ticker"], m)
    (OUT / "ffpts_markets.json").write_text(json.dumps(list(seen.values())))
    print("total", len(seen))


if __name__ == "__main__":
    main()
