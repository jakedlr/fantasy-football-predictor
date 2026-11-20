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


def test_each_fg_distance_edge_is_its_own_case():
    """One kick per case, so a wrong tier can't be hidden by an opposite error in the same total."""
    cases = contract.build()["scoring-cases.json"]["cases"]
    single = {c["stats"]["fg_made_yards"][0] for c in cases
              if c["kind"] == "kicker" and len(c["stats"].get("fg_made_yards", [])) == 1}
    for name in LEADERBOARD_RULES:
        for tier in load_rules(name).kicker["fg_made_by_distance"]:
            assert tier["min"] in single or tier["min"] == 0
            assert tier["max"] is None or tier["max"] in single


def test_dst_cases_include_fractional_values_between_tiers():
    """Projected Totals give fractional Points Allowed and Yards Allowed (current + mean × fraction left);
    a value between one tier's max and the next tier's min belongs to the lower tier."""
    cases = contract.build()["scoring-cases.json"]["cases"]
    pa = {c["stats"].get("points_allowed_all") for c in cases if c["kind"] == "dst"}
    ya = {c["stats"].get("yards_allowed") for c in cases if c["kind"] == "dst"}
    for name in LEADERBOARD_RULES:
        r = load_rules(name)
        for tier in r.dst["points_allowed_tiers"]:
            if tier["max"] is not None:
                assert tier["max"] + 0.5 in pa, f"{name}: no case at {tier['max'] + 0.5} points allowed"
        for tier in r.dst.get("yards_allowed_tiers", []):
            if tier["max"] is not None:
                assert tier["max"] + 0.5 in ya, f"{name}: no case at {tier['max'] + 0.5} yards allowed"


def test_sleeper_fg_buckets_each_fall_in_one_tier():
    """Sleeper reports FGs made in distance buckets; the TypeScript port scores each kick at its
    bucket's min. That is exact only if no bucket straddles a tier edge under any rule set."""
    buckets = contract.build()["scoring-rules.json"]["sleeperFgBuckets"]
    ranges = sorted((b["min"], b["max"]) for b in buckets.values())
    assert ranges[0][0] == 0 and ranges[-1][1] is None
    for (_, hi), (lo, _) in zip(ranges, ranges[1:]):
        assert lo == hi + 1, "buckets must follow on"
    for name in LEADERBOARD_RULES:
        tiers = load_rules(name).kicker["fg_made_by_distance"]
        for key, b in buckets.items():
            top = b["max"] if b["max"] is not None else 99
            pts = {float(score_kicker({"fg_made_yards": [y]}, load_rules(name))) for y in range(b["min"], top + 1)}
            assert len(pts) == 1, f"{name}: Sleeper bucket {key} straddles a tier edge ({tiers})"
