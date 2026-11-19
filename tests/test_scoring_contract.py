"""The scoring contract the Leaderboard's TypeScript scorer is tested against (ADR 0005).

`scripts/export_scoring_contract.py` writes the Scoring Rules and scored cases from `ffp.scoring`
into `site/contracts/`. The committed files must match what the Python scorer says today, so a
change to the rules or the scorer that isn't re-exported fails here.
"""

import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_scoring_contract as contract  # noqa: E402

from ffp.scoring import load_rules, score, score_dst, score_kicker  # noqa: E402

CONTRACTS = ROOT / "site" / "contracts"
LEADERBOARD_RULES = ("ppr", "half-ppr", "standard", "espn-league")


def test_committed_contract_matches_the_python_scorer():
    for name, payload in contract.build().items():
        committed = json.loads((CONTRACTS / name).read_text())
        assert committed == payload, f"site/contracts/{name} is stale: run scripts/export_scoring_contract.py"


def test_rules_cover_the_leaderboard_toggle():
    rules = contract.build()["scoring-rules.json"]["rules"]
    assert tuple(rules) == LEADERBOARD_RULES
    assert rules["half-ppr"]["offense"]["receptions"] == 0.5
    assert rules["espn-league"]["kicker"]["fg_made_by_distance"][-1] == {"min": 60, "max": None, "points": 6}


def test_every_case_is_scored_under_every_rule_set():
    cases = contract.build()["scoring-cases.json"]["cases"]
    kinds = {c["kind"] for c in cases}
    assert kinds == {"offense", "kicker", "dst"}
    for case in cases:
        assert set(case["expected"]) == set(LEADERBOARD_RULES)


@pytest.mark.parametrize("rule_name", LEADERBOARD_RULES)
def test_expected_points_are_the_python_scorers(rule_name):
    rules = load_rules(rule_name)
    scorer = {"offense": score, "kicker": score_kicker, "dst": score_dst}
    for case in contract.build()["scoring-cases.json"]["cases"]:
        expected = case["expected"][rule_name]
        assert math.isclose(expected, float(scorer[case["kind"]](case["stats"], rules)), abs_tol=1e-9)
        assert expected == round(expected, 6)


def test_cases_probe_tier_edges():
    """Every Points Allowed and Yards Allowed tier edge, and every FG distance edge, is a case."""
    cases = contract.build()["scoring-cases.json"]["cases"]
    pa = {c["stats"].get("points_allowed_all") for c in cases if c["kind"] == "dst"}
    ya = {c["stats"].get("yards_allowed") for c in cases if c["kind"] == "dst"}
    fg = {y for c in cases if c["kind"] == "kicker" for y in c["stats"].get("fg_made_yards", [])}
    for name in LEADERBOARD_RULES:
        r = load_rules(name)
        for tier in r.dst["points_allowed_tiers"]:
            assert tier["min"] in pa
        for tier in r.dst.get("yards_allowed_tiers", []):
            assert tier["min"] in ya and (tier["max"] is None or tier["max"] in ya)
        for tier in r.kicker["fg_made_by_distance"]:
            assert tier["min"] in fg or tier["min"] == 0
