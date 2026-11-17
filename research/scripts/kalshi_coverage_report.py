"""Coverage report over the market tables written by kalshi_coverage.py.

Joins each Kalshi event to an nflverse Game (date + teams) for week and kickoff,
and each player UUID to players.json (kalshi_players.py) for position / gsis_id.
Prints one markdown table per question; also writes data/kalshi/research/events.json.
"""
from __future__ import annotations

import collections
import json
import re
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_client import CACHE  # noqa: E402

MON = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
ALIAS = {"LA": ["LA", "LAR"], "JAX": ["JAX", "JAC"], "WAS": ["WAS", "WSH"]}
ET = ZoneInfo("America/New_York")


def schedule():
    import nflreadpy as nfl
    s = nfl.load_schedules([2025, 2026]).to_dicts()
    by_date = collections.defaultdict(list)
    for g in s:
        ko = datetime.fromisoformat(f"{g['gameday']}T{g['gametime']}").replace(tzinfo=ET)
        g["kickoff_utc"] = ko.astimezone(timezone.utc)
        by_date[g["gameday"]].append(g)
    return by_date


def match_event(code: str, by_date):
    m = re.match(r"(\d\d)([A-Z]{3})(\d\d)([A-Z]+)$", code)
    if not m:
        return None
    day = f"20{m[1]}-{MON[m[2]]:02d}-{m[3]}"
    teams = m[4]
    for g in by_date.get(day, []):
        for a in ALIAS.get(g["away_team"], [g["away_team"]]):
            for h in ALIAS.get(g["home_team"], [g["home_team"]]):
                if teams in (a + h, h + a):
                    return g
    return None


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def main():
    by_date = schedule()
    players = json.loads((CACHE / "players.json").read_text())
    rows = []
    unmatched = collections.Counter()
    for f in sorted((CACHE / "markets").glob("*.json")):
        s = f.stem
        ms = json.loads(f.read_text())
        if not ms:
            continue
        per_pg = collections.defaultdict(list)
        games, weeks = set(), collections.Counter()
        pos = collections.Counter()
        gsis_ok = tot_p = 0
        ingame = cancelled = 0
        for m in ms:
            code = m["event_ticker"].split("-", 1)[1]
            g = match_event(code, by_date)
            if not g:
                unmatched[s] += 1
                continue
            pid = (m.get("custom_strike") or {}).get("football_player")
            per_pg[(g["game_id"], pid)].append(m)
            games.add(g["game_id"])
            if m["open_time"] and datetime.fromisoformat(m["open_time"].replace("Z", "+00:00")) >= g["kickoff_utc"]:
                ingame += 1
            if m["result"] == "scalar" and m.get("market_type") != "scalar" and m.get("strike_type") != "custom":
                cancelled += 1
        pids = set()
        for (gid, pid), lst in per_pg.items():
            p = players.get(pid or "", {})
            pos[p.get("position") or "?"] += 1
            pids.add(pid)
            weeks[gid[:7]] += 1  # season_week
        for pid in pids:
            tot_p += 1
            gsis_ok += bool(players.get(pid or "", {}).get("gsis_id"))
        rungs = [len(v) for v in per_pg.values()]
        first = min(m["open_time"] for m in ms)[:10]
        last = max(m["open_time"] for m in ms)[:10]
        nweeks = len(weeks)
        rows.append(dict(series=s, markets=len(ms), games=len(games), weeks=nweeks,
                         player_games=len(per_pg), players=len(pids),
                         pg_per_week=round(len(per_pg) / max(nweeks, 1)),
                         mk_per_week=round(len(ms) / max(nweeks, 1)),
                         rungs_med=st.median(rungs), rungs_p10=q(rungs, .1), rungs_p90=q(rungs, .9),
                         pos=" ".join(f"{k}:{v}" for k, v in pos.most_common(5)),
                         gsis=f"{gsis_ok}/{tot_p}", ingame_pct=round(100 * ingame / len(ms), 1),
                         fairprice_pct=round(100 * cancelled / len(ms), 1), first=first, last=last,
                         weeklist=sorted(weeks)))
    cols = ["series", "first", "last", "weeks", "games", "player_games", "players", "markets",
            "pg_per_week", "mk_per_week", "rungs_med", "rungs_p10", "rungs_p90", "ingame_pct",
            "fairprice_pct", "gsis", "pos"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")
    print("unmatched events (markets):", dict(unmatched))
    for r in rows:
        print(r["series"], r["weeklist"])


if __name__ == "__main__":
    main()
