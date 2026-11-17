"""Can nflverse weekly player stats reproduce Sleeper's PPR points that Kalshi settles on?

Reads data/kalshi/research/ffpts_markets.json (from kalshi_ffpts_fetch.py), matches
each finalized non-D/ST market to an nflverse player-game, scores it with the
Sleeper default full-PPR formula from the markets' rules_secondary, and checks:

- KXNFLFFPTSLADDER from 2026-10-04 on: expiration_value is Sleeper's exact point
  total, so compare points directly. Earlier ladders only publish the cent payout:
  check round-half-up(points) == payout cents.
- KXNFLFFPTS ("Over X"): result yes <=> points > X.

Usage: .venv/bin/python research/scripts/kalshi_ffpts_reproduce.py
"""

import collections
import datetime as dt
import json
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

import nflreadpy

MARKETS = "data/kalshi/research/ffpts_markets.json"
KALSHI_TO_NFLVERSE_TEAM = {"JAC": "JAX", "LAR": "LA"}

# Sleeper default full-PPR (Kalshi rules_secondary), keyed by nflverse columns.
SLEEPER = {
    "passing_yards": 0.04,
    "passing_tds": 4,
    "passing_interceptions": -1,
    "passing_2pt_conversions": 2,
    "rushing_yards": 0.1,
    "rushing_tds": 6,
    "rushing_2pt_conversions": 2,
    "receptions": 1,
    "receiving_yards": 0.1,
    "receiving_tds": 6,
    "receiving_2pt_conversions": 2,
    "sack_fumbles_lost": -2,
    "rushing_fumbles_lost": -2,
    "receiving_fumbles_lost": -2,
    "special_teams_tds": 6,
    "fumble_recovery_tds": 6,
}

# The part our SCORING_STATS model (no special-teams / fumble-recovery TDs).
MODELLED = {k: v for k, v in SLEEPER.items() if k not in ("special_teams_tds", "fumble_recovery_tds")}


def norm(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name)
    return re.sub(r"[^a-z]", "", name)


def points(row: dict, formula: dict) -> float:
    return round(sum(w * (row.get(c) or 0) for c, w in formula.items()), 2)


def player_name(m: dict) -> str:
    t = m["title"]
    return t.split(":")[0] if ":" in t else t.replace(" fantasy scalar ladder", "")


def game_date(m: dict) -> dt.date:
    return dt.datetime.strptime(m["event_ticker"].split("-")[1][:7], "%y%b%d").date()


def load_nflverse():
    sched = nflreadpy.load_schedules(2026).to_dicts()
    week_of = {}  # (date, team) -> week
    for g in sched:
        if g["gameday"] is None:
            continue
        d = dt.date.fromisoformat(str(g["gameday"])[:10])
        for t in (g["home_team"], g["away_team"]):
            week_of[(d, t)] = g["week"]
    stats = nflreadpy.load_player_stats(2026, summary_level="week").to_dicts()
    by_key = collections.defaultdict(list)
    for r in stats:
        by_key[(r["week"], r["team"], norm(r["player_display_name"] or r["player_name"] or ""))].append(r)
    return week_of, by_key


KALSHI_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAC", "KC",
    "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
}


def player_team(teams: str, player: str) -> str:
    """Split an event's team pair ('GBTB') into codes and pick the one the player segment starts with."""
    for i in (2, 3):
        a, b = teams[:i], teams[i:]
        if a in KALSHI_TEAMS and b in KALSHI_TEAMS:
            return next(t for t in sorted((a, b), key=len, reverse=True) if player.startswith(t) and player[len(t):] != "")
    raise ValueError(teams)


def kicker_points(r: dict) -> float:
    """Sleeper default kicking: FG 0-39 +3, 40-49 +4, 50-59 +5, 60+ +6, PAT +1, missed FG/PAT -1."""
    g = lambda c: r.get(c) or 0  # noqa: E731
    made = 3 * (g("fg_made_0_19") + g("fg_made_20_29") + g("fg_made_30_39")) + 4 * g("fg_made_40_49")
    made += 5 * g("fg_made_50_59") + 6 * g("fg_made_60_")
    return made + g("pat_made") - g("fg_missed") - g("pat_missed")


def main() -> None:
    markets = [m for m in json.load(open(MARKETS)) if m["status"] in ("finalized", "settled")]
    week_of, by_key = load_nflverse()
    tally = collections.Counter()
    diffs, over_misses, unmatched, lags = [], [], [], []
    for m in markets:
        series, event, player = m["ticker"].split("-")[:3]
        if m.get("settlement_ts"):
            lag = dt.datetime.fromisoformat(m["settlement_ts"].replace("Z", "+00:00")) - dt.datetime.fromisoformat(
                m["occurrence_datetime"].replace("Z", "+00:00"))
            lags.append((series, lag.total_seconds() / 3600))
        if player.endswith("DST"):
            tally[series, "dst_skipped"] += 1
            continue
        team = player_team(event[7:], player)
        team = KALSHI_TO_NFLVERSE_TEAM.get(team, team)
        week = week_of.get((game_date(m), team))
        rows = by_key.get((week, team, norm(player_name(m))), [])
        if len(rows) != 1:
            tally[series, "no_nflverse_row"] += 1
            unmatched.append((m["ticker"], player_name(m), m["result"], m["expiration_value"]))
            continue
        r = rows[0]
        pos = r["position"]
        pts = round(points(r, SLEEPER) + (kicker_points(r) if pos == "K" else 0), 2)
        tally[series, pos, "matched"] += 1
        if series == "KXNFLFFPTSLADDER":
            ev = m["expiration_value"]
            cents = int(Decimal(m["settlement_value_dollars"]) * 100)
            exp_cents = max(0, min(100, int(Decimal(str(pts)).quantize(Decimal("1"), ROUND_HALF_UP))))
            if ev and not (ev.startswith("0.") and len(ev) == 4):
                tally[series, pos, "exact_checked"] += 1
                d = round(float(ev) - pts, 2)
                if abs(d) < 0.005:
                    tally[series, pos, "exact_agree"] += 1
                else:
                    tally[series, pos, "within_1pt"] += abs(d) <= 1
                    diffs.append((m["ticker"], pts, ev, d, extra(r)))
                tally[series, "nflverse_fantasy_points_ppr_agree"] += abs(float(ev) - (r["fantasy_points_ppr"] or 0)) < 0.005
                tally[series, "modelled_stats_only_agree"] += abs(float(ev) - points(r, MODELLED)) < 0.005
            tally[series, "cents_checked"] += 1
            tally[series, "cents_agree"] += cents == exp_cents
        else:
            yes = m["result"] == "yes"
            tally[series, pos, "over_checked"] += 1
            if yes == (pts > m["floor_strike"]):
                tally[series, pos, "over_agree"] += 1
            else:
                over_misses.append((m["ticker"], pts, m["floor_strike"], m["result"], extra(r)))
            if abs(pts - m["floor_strike"]) < 1e-9:
                tally[series, "points_equal_strike"] += 1
    for k in sorted(tally, key=str):
        print(k, tally[k])
    for s in ("KXNFLFFPTS", "KXNFLFFPTSLADDER"):
        hs = sorted(h for ser, h in lags if ser == s)
        if hs:
            print(s, "settlement lag after expected game end (h): min %.1f median %.1f max %.1f" % (hs[0], hs[len(hs) // 2], hs[-1]))
    print("\nLadder exact-value mismatches (Sleeper - ours):")
    for d in diffs:
        print(" ", d)
    print("\nOver-line mismatches:")
    for d in over_misses:
        print(" ", d)
    print("\nUnmatched:")
    for u in unmatched:
        print(" ", u)


def extra(r: dict) -> dict:
    keys = ("special_teams_tds", "fumble_recovery_tds", "fumbles_lost_total", "passing_interceptions",
            "passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions", "fg_att", "pat_att", "game_id")
    return {k: r.get(k) for k in keys if r.get(k)}


if __name__ == "__main__":
    main()
