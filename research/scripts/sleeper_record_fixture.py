"""Trim a saved Sleeper player list (https://api.sleeper.app/v1/players/nfl, ~15 MB) to a test fixture.

    curl -s -o players.json https://api.sleeper.app/v1/players/nfl
    python research/scripts/sleeper_record_fixture.py players.json research/fixtures/sleeper_players_nfl.json

Keeps the fantasy positions (QB/RB/WR/TE/K and DEF) on the fixture schedule's teams (BAL, LAC, CAR,
NYJ) plus the Rams (Sleeper's LAR is nflverse's LA), and only the fields `ffp.sources.sleeper` reads,
as Sleeper spells them (gsis_id with its stray leading space, espn_id as a number).
"""

import json
import sys

TEAMS = {"BAL", "LAC", "CAR", "NYJ", "LAR"}
POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
FIELDS = ("player_id", "full_name", "first_name", "last_name", "position", "team", "gsis_id", "espn_id", "status",
          "active")


def main(source: str, target: str) -> None:
    players = json.loads(open(source).read())
    kept = {pid: {f: p.get(f) for f in FIELDS if f in p} for pid, p in players.items()
            if p.get("team") in TEAMS and p.get("position") in POSITIONS}
    with open(target, "w") as out:
        json.dump(dict(sorted(kept.items())), out, separators=(",", ":"))
        out.write("\n")
    print(f"kept {len(kept)} of {len(players)} players")


if __name__ == "__main__":
    main(*sys.argv[1:3])
