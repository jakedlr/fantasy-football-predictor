"""Download all Kalshi football_player structured targets and map them to
nflverse gsis_id via sportradar_id (Kalshi `source_id` == nflverse `sportradar_id`).

Writes data/kalshi/research/players.json: {kalshi_uuid: {name, position, abbreviation,
jersey, league, sportradar_id, gsis_id}}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_client import CACHE, STATS, get  # noqa: E402


def all_targets(kind: str) -> list[dict]:
    out, cursor = [], None
    while True:
        p = {"type": kind, "page_size": 2000}
        if cursor:
            p["cursor"] = cursor
        d = get("/structured_targets", p)
        out += d.get("structured_targets") or []
        cursor = d.get("cursor")
        if not cursor or not d.get("structured_targets"):
            return out


def sportradar_to_gsis() -> dict[str, str]:
    import nflreadpy as nfl
    import polars as pl
    m = {}
    for season in (2025, 2026):
        r = nfl.load_rosters(season).filter(pl.col("sportradar_id").is_not_null())
        m.update(dict(zip(r["sportradar_id"], r["gsis_id"])))
    return m


if __name__ == "__main__":
    ts = all_targets("football_player")
    sr = sportradar_to_gsis()
    players = {}
    for t in ts:
        d = t.get("details") or {}
        if d.get("league") != "NFL":
            continue
        sid = t.get("source_id") or (t.get("source_ids") or {}).get("source_3_id")
        players[t["id"]] = {"name": t.get("name"), "position": d.get("position"),
                            "abbreviation": d.get("abbreviation"), "jersey": d.get("jersey"),
                            "team_id": d.get("team_id"), "sportradar_id": sid, "gsis_id": sr.get(sid)}
    (CACHE / "players.json").write_text(json.dumps(players))
    n = len(players); g = sum(1 for p in players.values() if p["gsis_id"])
    print(f"targets={len(ts)} nfl_players={n} with_gsis={g}", STATS)
