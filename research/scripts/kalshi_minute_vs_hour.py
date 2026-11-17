"""Compare the mid at the last hourly candle ending <= kickoff with the mid at the
last 1-minute candle ending <= kickoff, for live Week 2026_09 Rungs (needs
price_sample.json from kalshi_prices.py)."""
from __future__ import annotations

import collections
import json
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_client import CACHE, STATS, get  # noqa: E402
from kalshi_coverage_report import match_event, schedule  # noqa: E402
from kalshi_prices import norm  # noqa: E402

SERIES = {"KXNFLRECYDS", "KXNFLREC", "KXNFLRSHYDS", "KXNFLPASSYDS"}

by_date = schedule()
rows = [r for r in json.loads((CACHE / "price_sample.json").read_text())
        if r["src"] == "live" and r["series"] in SERIES and r["game_id"].startswith("2026_09") and r["price"]]
ev = collections.defaultdict(list)
for r in rows:
    ev[r["ticker"].rsplit("-", 2)[0]].append(r)
diffs, stale, gap = collections.defaultdict(list), [], []
for et, rs in ev.items():
    g = match_event(et.split("-", 1)[1], by_date)
    ko = int(g["kickoff_utc"].timestamp())
    for i in range(0, len(rs), 100):
        chunk = rs[i:i + 100]
        d = get("/markets/candlesticks", {"market_tickers": ",".join(r["ticker"] for r in chunk),
                                          "start_ts": ko - 3600, "end_ts": ko, "period_interval": 1})
        got = {x["market_ticker"]: [norm(c) for c in x["candlesticks"]] for x in d.get("markets", [])}
        for r in chunk:
            cs = [c for c in got.get(r["ticker"], []) if c["end"] <= ko]
            h = r["price"]
            if not cs or not h["bid"] or not h["ask"] or h["ask"] >= 1:
                continue
            m = cs[-1]
            if not m["bid"] or m["ask"] is None or m["ask"] >= 1:
                continue
            hm, mm = (h["bid"] + h["ask"]) / 2, (m["bid"] + m["ask"]) / 2
            diffs[h["stale_min"]].append(abs(hm - mm))
            gap.append((ko - m["end"]) // 60)
print("n per hourly staleness (min) -> median / p90 |hourly mid - minute mid|")
for k in sorted(diffs):
    v = sorted(diffs[k])
    print(f"  stale {k:>3} min: n={len(v)} med={st.median(v):.3f} p90={v[int(.9*len(v))]:.3f}")
print("minute candle gap to kickoff (min): median", st.median(gap), "p90", sorted(gap)[int(.9 * len(gap))], STATS)
