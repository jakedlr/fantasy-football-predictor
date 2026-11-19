"""Export the Scoring Rules and scored cases the Leaderboard's TypeScript scorer must match (ADR 0005).

    python scripts/export_scoring_contract.py

writes `site/contracts/scoring-rules.json` and `site/contracts/scoring-cases.json` from `ffp.scoring`.
`tests/test_scoring_contract.py` fails when the committed files are stale.
"""

import json
from pathlib import Path

from ffp.scoring import load_rules, score, score_dst, score_kicker

OUT = Path(__file__).resolve().parents[1] / "site" / "contracts"
# The Leaderboard's Scoring Rules toggle, default first (PLAN, Leaderboard site).
LEADERBOARD_RULES = ("ppr", "half-ppr", "standard", "espn-league")
_SCORERS = {"offense": score, "kicker": score_kicker, "dst": score_dst}


def _offense_cases() -> list[dict]:
    return [
        ("empty stat line", {}),
        ("QB line", {"passing_yards": 287, "passing_tds": 2, "passing_interceptions": 1,
                     "rushing_yards": 23, "fumbles_lost": 1}),
        ("RB line with a 2-pt run", {"rushing_yards": 104, "rushing_tds": 1, "rushing_2pt": 1,
                                     "receptions": 3, "receiving_yards": 19}),
        ("WR line", {"receptions": 7, "receiving_yards": 96, "receiving_tds": 1, "receiving_2pt": 1}),
        ("negative yards", {"rushing_yards": -4, "receptions": 1, "receiving_yards": -2}),
        ("QB 2-pt pass, 2 fumbles", {"passing_yards": 3, "passing_2pt": 1, "fumbles_lost": 2}),
        ("fractional projection", {"receptions": 4.3, "receiving_yards": 51.7, "receiving_tds": 0.38,
                                   "rushing_yards": 2.1, "fumbles_lost": 0.04}),
    ]


def _kicker_cases() -> list[dict]:
    return [
        ("no kicks", {}),
        ("PATs only", {"pat_made": 4, "pat_missed": 1}),
        ("FG distance edges", {"fg_made_yards": [19, 39, 40, 49, 50, 59, 60, 64]}),
        ("misses", {"fg_made_yards": [33], "fg_missed": 2, "pat_made": 1}),
    ]


def _dst_cases() -> list[dict]:
    edges = sorted({0, 1, 6, 7, 13, 14, 17, 18, 27, 28, 34, 35, 45, 46, 60})
    yard_edges = sorted({-12, 0, 99, 100, 199, 200, 299, 300, 349, 350, 399, 400, 449, 450, 499,
                         500, 549, 550, 612})
    cases = [(f"{pa} points allowed", {"points_allowed_all": pa, "yards_allowed": 320}) for pa in edges]
    cases += [(f"{ya} yards allowed", {"points_allowed_all": 20, "yards_allowed": ya}) for ya in yard_edges]
    cases += [
        ("big day", {"points_allowed_all": 10, "yards_allowed": 250, "sacks": 4, "interceptions": 2,
                     "fumble_recoveries": 1, "forced_fumbles": 2, "safeties": 1, "blocked_kicks": 1,
                     "interception_return_tds": 1, "punt_return_tds": 1}),
        ("opponent pick-six left out of Points Allowed",
         {"points_allowed_all": 21, "opponent_defensive_tds_points": 7, "yards_allowed": 280}),
        ("opponent return TD", {"points_allowed_all": 24, "opponent_return_tds_points": 7,
                                "yards_allowed": 310, "kickoff_return_tds": 1, "fumble_return_tds": 1,
                                "blocked_kick_return_tds": 1}),
    ]
    return cases


def _rules_payload(name: str) -> dict:
    r = load_rules(name)
    return {"offense": dict(r.offense), "kicker": r.kicker, "dst": r.dst, "assumptions": list(r.assumptions)}


def build() -> dict[str, dict]:
    """The contract files, by file name."""
    rules = {name: load_rules(name) for name in LEADERBOARD_RULES}
    groups = {"offense": _offense_cases(), "kicker": _kicker_cases(), "dst": _dst_cases()}
    cases = [
        {
            "kind": kind,
            "name": name,
            "stats": stats,
            "expected": {r: round(float(_SCORERS[kind](stats, rules[r])), 6) for r in LEADERBOARD_RULES},
        }
        for kind, group in groups.items()
        for name, stats in group
    ]
    header = {"generatedBy": "scripts/export_scoring_contract.py", "schemaVersion": 1}
    return {
        "scoring-rules.json": {**header, "default": LEADERBOARD_RULES[0],
                               "rules": {name: _rules_payload(name) for name in LEADERBOARD_RULES}},
        "scoring-cases.json": {**header, "cases": cases},
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, payload in build().items():
        (OUT / name).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
