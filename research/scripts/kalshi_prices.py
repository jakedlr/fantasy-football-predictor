"""Sample pre-kickoff prices for Kalshi player-prop Rungs and measure spreads,
volume and one-sided books at the last pre-kickoff hourly candle.

Historical markets (settled before /historical/cutoff): one request per market,
GET /historical/markets/{ticker}/candlesticks (fields: close, volume, open_interest).
Live markets: batched, GET /markets/candlesticks?market_tickers=<<=100> (fields:
close_dollars, volume_fp, open_interest_fp).

Writes data/kalshi/research/price_sample.json and prints summary tables.
"""
from __future__ import annotations

import collections
import json
import random
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_client import CACHE, STATS, get  # noqa: E402
from kalshi_coverage_report import match_event, schedule  # noqa: E402

HIST_SERIES = ["KXNFLRECYDS", "KXNFLREC", "KXNFLRSHYDS", "KXNFLPASSYDS", "KXNFLPASSTDS", "KXNFLANYTD", "KXNFL2TD"]
LIVE_SERIES = ["KXNFLRECYDS", "KXNFLREC", "KXNFLRSHYDS", "KXNFLRSHATT", "KXNFLPASSYDS", "KXNFLPASSTDS",
               "KXNFLPASSINT", "KXNFLTD", "KXNFLFFPTS", "KXNFLRECYDSPROT", "KXNFLRSHYDSPROT"]
LIVE_WEEKS = {"2026_04", "2026_09"}
HIST_PG_PER_SERIES = 30
WINDOW_H = 24


def ts(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def f(x):
    return None if x in (None, "") else float(x)


def norm(c: dict) -> dict:
    """Normalise historical vs live candle field names."""
    ya, yb, pr = c.get("yes_ask") or {}, c.get("yes_bid") or {}, c.get("price") or {}
    g = lambda d, k: f(d.get(k + "_dollars", d.get(k)))  # noqa: E731
    return {"end": c["end_period_ts"], "bid": g(yb, "close"), "ask": g(ya, "close"),
            "last": g(pr, "close"), "vol": f(c.get("volume_fp", c.get("volume"))) or 0.0,
            "oi": f(c.get("open_interest_fp", c.get("open_interest"))) or 0.0}


def summarise(cands: list[dict], ko: int) -> dict | None:
    pre = [c for c in cands if c["end"] <= ko]
    if not pre:
        return None
    last = pre[-1]
    return {"bid": last["bid"], "ask": last["ask"], "last_trade": last["last"],
            "stale_min": (ko - last["end"]) // 60, "vol_last_h": last["vol"] if ko - last["end"] < 3600 else 0.0,
            "vol_24h": sum(c["vol"] for c in pre), "oi": last["oi"]}


def load(series):
    return json.loads((CACHE / "markets" / f"{series}.json").read_text())


def main():
    random.seed(7)
    by_date = schedule()
    out = []
    # live: whole weeks, batched by event
    for s in LIVE_SERIES:
        ev = collections.defaultdict(list)
        for m in load(s):
            if m["source"] != "live":
                continue
            g = match_event(m["event_ticker"].split("-", 1)[1], by_date)
            if not g or g["game_id"][:7] not in LIVE_WEEKS:
                continue
            ko = int(g["kickoff_utc"].timestamp())
            if ts(m["open_time"]) >= ko:
                continue
            ev[(m["event_ticker"], ko, g["game_id"])].append(m)
        for (et, ko, gid), ms in ev.items():
            for i in range(0, len(ms), 100):
                chunk = ms[i:i + 100]
                d = get("/markets/candlesticks", {"market_tickers": ",".join(m["ticker"] for m in chunk),
                                                  "start_ts": ko - WINDOW_H * 3600, "end_ts": ko, "period_interval": 60})
                got = {x["market_ticker"] if "market_ticker" in x else x.get("ticker"): x for x in d.get("markets", [])}
                for m in chunk:
                    x = got.get(m["ticker"])
                    r = summarise([norm(c) for c in (x or {}).get("candlesticks", [])], ko)
                    out.append({"series": s, "src": "live", "ticker": m["ticker"], "game_id": gid,
                                "player": (m["custom_strike"] or {}).get("football_player"),
                                "strike": m["floor_strike"], "result": m["result"], "price": r})
        print(s, "live done", STATS, file=sys.stderr)
    # historical: random player-games, one request per market
    for s in HIST_SERIES:
        pg = collections.defaultdict(list)
        for m in load(s):
            if m["source"] != "historical":
                continue
            g = match_event(m["event_ticker"].split("-", 1)[1], by_date)
            if not g:
                continue
            ko = int(g["kickoff_utc"].timestamp())
            if ts(m["open_time"]) >= ko:
                continue
            pg[(g["game_id"], ko, (m["custom_strike"] or {}).get("football_player"))].append(m)
        keys = sorted(pg)
        for key in random.sample(keys, min(HIST_PG_PER_SERIES, len(keys))):
            gid, ko, pid = key
            for m in pg[key]:
                d = get(f"/historical/markets/{m['ticker']}/candlesticks",
                        {"start_ts": ko - WINDOW_H * 3600, "end_ts": ko, "period_interval": 60})
                r = summarise([norm(c) for c in d.get("candlesticks") or []], ko)
                out.append({"series": s, "src": "historical", "ticker": m["ticker"], "game_id": gid, "player": pid,
                            "strike": m["floor_strike"], "result": m["result"], "price": r})
        print(s, "hist done", STATS, file=sys.stderr)
    (CACHE / "price_sample.json").write_text(json.dumps(out))
    report(out)


def bucket(mid):
    for hi, name in ((0.05, "<5%"), (0.15, "5-15%"), (0.35, "15-35%"), (0.65, "35-65%"), (0.85, "65-85%"), (0.95, "85-95%")):
        if mid < hi:
            return name
    return ">95%"


def report(out):
    print("| series | src | rungs | no candle | no bid | no ask | two-sided | med spread (2-sided) | p90 spread | med vol 24h | zero vol 24h | med stale min |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    groups = collections.defaultdict(list)
    for r in out:
        groups[(r["series"], r["src"])].append(r)
    for (s, src), rs in sorted(groups.items()):
        p = [r["price"] for r in rs if r["price"]]
        nob = sum(1 for x in p if not x["bid"])
        noa = sum(1 for x in p if x["ask"] is None or x["ask"] >= 1)
        two = [x for x in p if x["bid"] and x["ask"] is not None and x["ask"] < 1]
        sp = sorted(x["ask"] - x["bid"] for x in two)
        v = sorted(x["vol_24h"] for x in p)
        print(f"| {s} | {src} | {len(rs)} | {len(rs)-len(p)} | {nob} | {noa} | {len(two)} ({100*len(two)/max(len(rs),1):.0f}%) | "
              f"{st.median(sp) if sp else '-':.2f} | {sp[int(.9*len(sp))] if sp else '-':.2f} | {st.median(v):.0f} | "
              f"{sum(1 for x in v if x == 0)} | {st.median([x['stale_min'] for x in p]):.0f} |")
    print()
    print("By mid-probability bucket (all series, two-sided rungs; mid = (bid+ask)/2):")
    print("| bucket | rungs | med spread | p90 spread | med vol 24h | share one-sided (of all rungs in bucket by last trade/mid) |")
    print("|---|---|---|---|---|---|")
    b = collections.defaultdict(list)
    for r in out:
        x = r["price"]
        if not x:
            continue
        bid, ask = x["bid"] or 0.0, 1.0 if x["ask"] is None else x["ask"]
        b[bucket((bid + ask) / 2)].append(x)
    for name in ["<5%", "5-15%", "15-35%", "35-65%", "65-85%", "85-95%", ">95%"]:
        xs = b.get(name, [])
        two = [x for x in xs if x["bid"] and x["ask"] is not None and x["ask"] < 1]
        sp = sorted(x["ask"] - x["bid"] for x in two)
        v = sorted(x["vol_24h"] for x in xs)
        if xs:
            print(f"| {name} | {len(xs)} | {st.median(sp) if sp else float('nan'):.2f} | {sp[int(.9*len(sp))] if sp else float('nan'):.2f} | "
                  f"{st.median(v):.0f} | {100*(1-len(two)/len(xs)):.0f}% |")


if __name__ == "__main__":
    if sys.argv[1:] == ["--report"]:
        report(json.loads((CACHE / "price_sample.json").read_text()))
    else:
        main()
