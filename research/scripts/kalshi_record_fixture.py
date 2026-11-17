"""Record research/fixtures/kalshi/ (used by tests/test_kalshi_pull.py) by running
ffp.sources.kalshi.pull against the real API, trimmed to one 2025 Game (historical endpoints)
and one 2026 Game (live endpoints), a few players per series, and the fields the module reads.
About 115 requests. Rerun it whenever pull() changes the URLs it asks for.

Usage: .venv/bin/python research/scripts/kalshi_record_fixture.py
"""
import collections
import csv
import json
import sys
import tempfile
import urllib.parse
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "src"))
from ffp.sources import kalshi  # noqa: E402

OUT = WT / "research" / "fixtures" / "kalshi"
EVENT = {"historical": "25DEC01NYGNE", "live": "26NOV15PITCIN"}
GAMES = {2025: "2025_13_NYG_NE", 2026: "2026_10_PIT_CIN"}
real = kalshi.KalshiHttp()
recorded = {}
uuids = set()


def trim_market(m):
    return {"ticker": m["ticker"], "custom_strike": {"football_player": (m.get("custom_strike") or {}).get("football_player")},
            "floor_strike": m.get("floor_strike"), "open_time": m.get("open_time"),
            "settlement_ts": m.get("settlement_ts"), "result": m.get("result"),
            "expiration_value": m.get("expiration_value")}


def pick(markets):
    by = collections.defaultdict(list)
    for m in markets:
        by[(m.get("custom_strike") or {}).get("football_player")].append(m)
    code = {u: kalshi.parse_ticker(ms[0]["ticker"]).player or "" for u, ms in by.items()}
    ranked = sorted(by, key=lambda u: (-("JCHASE" in code[u] or "DSINGLETARY" in code[u]), -len(by[u]), code[u]))
    keep = ranked[:2]
    odd = [u for u in by if not any(c.isdigit() for c in code[u])]  # D/ST, No TD: no jersey
    keep += odd[:1]
    return [m for u in keep for m in by[u]]


def trim_candle(c):
    out = {"end_period_ts": c["end_period_ts"]}
    for side in ("yes_bid", "yes_ask"):
        s = c.get(side) or {}
        out[side] = {k: v for k, v in s.items() if k in ("close", "close_dollars")}
    for k in ("volume", "volume_fp"):
        if k in c:
            out[k] = c[k]
    return out


def trim_target(t):
    d = t.get("details") or {}
    return {"id": t["id"], "type": t.get("type"), "name": t.get("name"), "source_id": t.get("source_id"),
            "details": {"league": d.get("league"), "position": d.get("position")}}


class Recorder:
    @property
    def requests(self):
        return real.requests

    def get_json(self, url):
        parts = urllib.parse.urlsplit(url)
        path = parts.path.removeprefix("/trade-api/v2")
        q = dict(urllib.parse.parse_qsl(parts.query))
        if path in ("/markets", "/historical/markets"):
            src = "historical" if path.startswith("/historical") else "live"
            event = f"{q['series_ticker']}-{EVENT[src]}"
            p = {"event_ticker": event, "limit": 1000}
            if src == "live":
                p["status"] = "settled"
            got = real.get_json(kalshi.api_url(path, **p)).get("markets") or []
            ms = [trim_market(m) for m in pick(got)]
            uuids.update(m["custom_strike"]["football_player"] for m in ms)
            data = {"markets": ms, "cursor": ""}
        elif path == "/structured_targets":
            ts = []
            for u in sorted(uuids):
                try:
                    t = real.get_json(kalshi.api_url(f"/structured_targets/{u}"))["structured_target"]
                except kalshi.NotFound:
                    continue
                if t.get("type") == "football_player":
                    ts.append(trim_target(t))
            data = {"structured_targets": ts, "cursor": ""}
        elif path.startswith("/structured_targets/"):
            try:
                data = {"structured_target": trim_target(real.get_json(url)["structured_target"])}
            except kalshi.NotFound:
                recorded[url] = {"_status": 404}
                raise
        elif path.endswith("/candlesticks"):
            data = real.get_json(url)
            if "markets" in data:
                data = {"markets": [{"market_ticker": x["market_ticker"],
                                     "candlesticks": [trim_candle(c) for c in x["candlesticks"][-3:]]}
                                    for x in data["markets"]]}
            else:
                data = {"ticker": data.get("ticker"), "candlesticks": [trim_candle(c) for c in data["candlesticks"][-3:]]}
        else:
            data = real.get_json(url)
        recorded[url] = data
        return data


sched_rows, roster_rows = [], []
SCHED_COLS = ["game_id", "season", "game_type", "week", "gameday", "gametime", "away_team", "home_team",
              "away_score", "home_score"]


def schedule_fetch(season):
    import nflreadpy
    rows = [r for r in nflreadpy.load_schedules(season).to_dicts() if r["game_id"] == GAMES[season]]
    sched_rows.extend({k: r[k] for k in SCHED_COLS} for r in rows)
    return rows


def roster_fetch(season):
    import nflreadpy
    ids = {t["source_id"] for d in recorded.values() for t in d.get("structured_targets", [])}
    rows = [r for r in nflreadpy.load_rosters(season).to_dicts() if r.get("sportradar_id") in ids]
    roster_rows.extend({k: r[k] for k in ("season", "sportradar_id", "gsis_id", "full_name")} for r in rows)
    return rows


with tempfile.TemporaryDirectory() as root:
    report = kalshi.pull(root, [2025, 2026], http=Recorder(), schedule_fetch=schedule_fetch,
                         roster_fetch=roster_fetch)
    print(kalshi.format_report(report))

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "responses.json").write_text(json.dumps(recorded, indent=1, sort_keys=True) + "\n")
for name, rows, cols in (("schedule.csv", sched_rows, SCHED_COLS),
                         ("rosters.csv", roster_rows, ["season", "sportradar_id", "gsis_id", "full_name"])):
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(rows)
print("requests", real.requests, "responses", len(recorded))
