import json
from pathlib import Path

import pytest

from ffp.scoring import DEFAULT_RULES, SCORING_STATS, load_rules, score

# The bundled default by path, so a scoring/espn-league.json in the working directory can't change it.
ESPN_FILE = Path(__file__).resolve().parents[1] / "src" / "ffp" / "rules" / "espn-league.json"

# A WR stat line: 6 receptions, 85 receiving yards, 1 receiving TD, 1 fumble lost.
WR_LINE = {"receptions": 6, "receiving_yards": 85, "receiving_tds": 1, "fumbles_lost": 1}


def test_standard_preset_scores_wr_line():
    # 85 * 0.1 = 8.5 rec yds; 1 * 6 = 6 rec TD; 6 * 0 = 0 receptions; 1 * -2 = -2 fumble
    # 8.5 + 6 + 0 - 2 = 12.5
    assert score(WR_LINE, load_rules("standard")) == pytest.approx(12.5)


@pytest.mark.parametrize(
    "preset, expected",
    [
        # 12.5 (standard total) + 6 * 0.5 receptions = 15.5
        ("half-ppr", 15.5),
        # 12.5 (standard total) + 6 * 1 receptions = 18.5
        ("ppr", 18.5),
    ],
)
def test_reception_presets_score_wr_line(preset, expected):
    assert score(WR_LINE, load_rules(preset)) == pytest.approx(expected)


def test_espn_league_scores_wr_line():
    # Full PPR: 6 * 1 receptions = 6; 85 * 0.1 = 8.5 rec yds; 1 * 6 = 6 rec TD; 1 * -2 = -2 fumble
    # 6 + 8.5 + 6 - 2 = 18.5
    rules = load_rules(ESPN_FILE)
    assert rules.name == "espn-league"
    assert score(WR_LINE, rules) == pytest.approx(18.5)


def test_espn_league_scores_qb_line():
    qb_line = {"passing_yards": 275, "passing_tds": 2, "passing_interceptions": 1, "rushing_yards": 20}
    # 275 * 0.04 = 11 pass yds; 2 * 4 = 8 pass TD; 1 * -2 = -2 INT; 20 * 0.1 = 2 rush yds
    # 11 + 8 - 2 + 2 = 19
    rules = load_rules(ESPN_FILE)
    assert score(qb_line, rules) == pytest.approx(19)


def write_rules(tmp_path, data, name="custom"):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(data))
    return path


def test_unknown_scoring_stat_is_rejected(tmp_path):
    path = write_rules(tmp_path, {"name": "custom", "offense": {"receptions": 1, "longest_reception": 1}})
    with pytest.raises(ValueError, match="longest_reception"):
        load_rules(path)


def test_missing_offense_stats_default_to_zero(tmp_path):
    path = write_rules(tmp_path, {"name": "td-only", "offense": {"receiving_tds": 6}})
    rules = load_rules(path)
    assert set(rules.offense) == set(SCORING_STATS)
    assert rules.offense["receptions"] == 0
    # Only the TD scores: 1 * 6 = 6
    assert score(WR_LINE, rules) == pytest.approx(6)


def test_espn_league_kicker_and_dst_sections_load():
    rules = load_rules(ESPN_FILE)
    assert rules.kicker["pat_made"] == 1
    assert rules.kicker["fg_missed"] == -1
    assert rules.kicker["fg_made_by_distance"][-1] == {"min": 60, "max": None, "points": 6}
    assert rules.dst["sacks"] == 1
    assert rules.dst["punt_return_tds"] == 6
    assert rules.dst["points_allowed_tiers"][0] == {"min": 0, "max": 0, "points": 5}
    assert rules.dst["yards_allowed_tiers"][-1] == {"min": 550, "max": None, "points": -7}
    assert any("18-27" in a for a in rules.assumptions)


def test_kicker_and_dst_do_not_affect_offense_score(tmp_path):
    data = {"name": "k-heavy", "offense": {"receiving_tds": 6}, "kicker": {"pat_made": 100}, "dst": {"sacks": 100}}
    rules = load_rules(write_rules(tmp_path, data))
    # Only the TD scores: 1 * 6 = 6, even if the stat line carries K/DST-looking keys
    assert score({**WR_LINE, "pat_made": 1, "sacks": 1}, rules) == pytest.approx(6)


def test_unknown_rules_name_errors(tmp_path):
    with pytest.raises(ValueError, match="no-such-league"):
        load_rules("no-such-league", scoring_dir=tmp_path)


def test_numpy_samples_score_elementwise():
    import numpy as np
    samples = {
        "receptions": np.array([0, 6, 10]),
        "receiving_yards": np.array([0.0, 85.0, 120.0]),
        "receiving_tds": np.array([0, 1, 2]),
        "fumbles_lost": np.array([0, 1, 0]),
    }
    points = score(samples, load_rules("ppr"))
    # Sample 0: all zeros = 0
    # Sample 1: the WR line under ppr = 18.5
    # Sample 2: 10 * 1 + 120 * 0.1 + 2 * 6 + 0 * -2 = 10 + 12 + 12 = 34
    assert isinstance(points, np.ndarray)
    np.testing.assert_allclose(points, [0, 18.5, 34])


def test_default_rules_load_from_any_working_directory(tmp_path, monkeypatch):
    # Review finding: "espn-league" was only found from the repo root.
    monkeypatch.chdir(tmp_path)
    rules = load_rules(DEFAULT_RULES)
    assert rules.name == "espn-league"
    assert rules.offense["receptions"] == 1


def test_a_local_scoring_file_overrides_the_bundled_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scoring").mkdir()
    (tmp_path / "scoring" / "espn-league.json").write_text(
        '{"name": "espn-league", "offense": {"receptions": 0.5}}'
    )
    assert load_rules("espn-league").offense["receptions"] == 0.5


def test_a_bare_name_is_looked_up_in_scoring_dir(tmp_path):
    # Review finding: no test used a scoring_dir different from the bundled one.
    write_rules(tmp_path, {"name": "my-league", "offense": {"receptions": 0.25}}, name="my-league")
    assert load_rules("my-league", scoring_dir=tmp_path).offense["receptions"] == 0.25


def test_a_bare_name_ignores_a_same_named_file_in_the_working_directory(tmp_path, monkeypatch):
    # Review finding: a notes file named "espn-league" took over the default lookup.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "espn-league").write_text("not JSON")
    assert load_rules("espn-league").name == "espn-league"


def test_a_missing_path_names_only_that_path(tmp_path):
    # Review finding: the error listed made-up candidates like scoring/leagues/my.json.json.
    missing = str(tmp_path / "leagues" / "my.json")
    with pytest.raises(ValueError) as raised:
        load_rules(missing)
    message = str(raised.value)
    assert missing in message
    assert ".json.json" not in message and "rules" not in message


def test_a_json_name_in_the_working_directory_is_read_as_a_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_rules(tmp_path, {"name": "here", "offense": {"receptions": 2}}, name="here")
    assert load_rules("here.json").offense["receptions"] == 2


def test_sleeper_preset_is_ppr_with_minus_one_interceptions():
    # Kalshi's fantasy-points markets settle on Sleeper's default full PPR
    # (research/kalshi-fantasy-points.md): the ppr preset but an INT costs -1.
    assert dict(load_rules("sleeper").offense) == {
        "passing_yards": 0.04,
        "passing_tds": 4,
        "passing_interceptions": -1,
        "passing_2pt": 2,
        "rushing_yards": 0.1,
        "rushing_tds": 6,
        "rushing_2pt": 2,
        "receptions": 1,
        "receiving_yards": 0.1,
        "receiving_tds": 6,
        "receiving_2pt": 2,
        "fumbles_lost": -2,
    }


def test_sleeper_preset_scores_qb_game():
    # 287 pass yds, 2 pass TD, 2 INT, 1 pass 2-pt, 31 rush yds, 1 rush TD, 1 fumble lost
    # 287 * 0.04 = 11.48; 2 * 4 = 8; 2 * -1 = -2; 1 * 2 = 2; 31 * 0.1 = 3.1; 6; -2
    # 11.48 + 8 - 2 + 2 + 3.1 + 6 - 2 = 26.58
    line = {
        "passing_yards": 287,
        "passing_tds": 2,
        "passing_interceptions": 2,
        "passing_2pt": 1,
        "rushing_yards": 31,
        "rushing_tds": 1,
        "fumbles_lost": 1,
    }
    assert score(line, load_rules("sleeper")) == pytest.approx(26.58)


# --- S6.1: kickers and D/ST ---------------------------------------------------------

import numpy as np  # noqa: E402

from ffp.scoring import PRESETS, ScoringRules, points_allowed, score_dst, score_kicker  # noqa: E402


def test_kicker_scores_fgs_by_distance_and_pats():
    # 3 PATs, FGs of 33, 47 and 52 yards, one miss: 3 + (3 + 4 + 5) - 1 = 14.
    line = {"pat_made": 3, "fg_missed": 1, "fg_made_yards": [33, 47, 52, np.nan]}
    assert score_kicker(line, load_rules(ESPN_FILE)) == 14.0


def test_kicker_samples_score_row_by_row():
    line = {"pat_made": np.array([1, 2]), "pat_missed": np.array([1, 0]),
            "fg_made_yards": np.array([[61.0, np.nan], [20.0, 30.0]])}
    # Sleeper: 1 - 1 + 6 = 6; 2 + 3 + 3 = 8. ESPN's league: no missed-PAT penalty, 60+ is 6.
    assert list(score_kicker(line, load_rules("sleeper"))) == [6.0, 8.0]
    assert list(score_kicker(line, load_rules(ESPN_FILE))) == [7.0, 8.0]
    # ESPN's default kicker tops out at 5 for 50+.
    assert list(score_kicker(line, load_rules("standard"))) == [6.0, 8.0]


DST_LINE = {"sacks": 3, "interceptions": 1, "forced_fumbles": 1, "points_allowed_all": 20,
            "opponent_defensive_tds_points": 7, "opponent_return_tds_points": 0, "yards_allowed": 310}


def test_dst_scores_counts_and_tiers():
    # ESPN league: 3 sacks + 2 INT; Points Allowed 13 (a pick-six left out) -> 3; 310 yards -> 0.
    assert score_dst(DST_LINE, load_rules(ESPN_FILE)) == 8.0
    # Sleeper: 3 + 2 + 1 forced fumble; 13 -> 4; no yards tiers.
    assert score_dst(DST_LINE, load_rules("sleeper")) == 10.0


def test_points_allowed_exclusions(tmp_path):
    rules = load_rules("sleeper")
    assert points_allowed(DST_LINE, rules) == 13
    data = {"name": "all-points", "offense": {}, "dst": {"points_allowed_excludes": [],
                                                         "points_allowed_tiers": [{"min": 0, "max": None, "points": 0}]}}
    assert points_allowed(DST_LINE, load_rules(write_rules(tmp_path, data))) == 20


def test_dst_tiers_cover_negative_yards_and_the_open_top():
    rules = load_rules(ESPN_FILE)
    yards = np.array([-12, 99, 100, 549, 900])
    got = score_dst({"points_allowed_all": np.full(5, 50), "yards_allowed": yards}, rules)
    assert list(got) == [5 - 5, 5 - 5, 3 - 5, -6 - 5, -7 - 5]


def test_every_preset_scores_kickers_and_dst():
    for rules in PRESETS.values():
        assert rules.kicker["fg_made_by_distance"] and rules.dst["points_allowed_tiers"]


@pytest.mark.parametrize("section, bad, match", [
    ("kicker", {"pat_made": 1, "field_goals": 3}, "unknown kicker rule"),
    ("dst", {"points_allowed_excludes": ["everything"]}, "points_allowed_excludes"),
    ("dst", {"points_allowed_tiers": [{"min": 7, "max": None, "points": 0}, {"min": 0, "max": 6, "points": 4}]},
     "only the last tier"),
    # Review of PRs 9-11: a skipped tier used to score as the tier below it.
    ("dst", {"yards_allowed_tiers": [{"min": 0, "max": 299, "points": 2}, {"min": 350, "max": None, "points": -1}]},
     "no gap or overlap"),
    ("dst", {"points_allowed_tiers": [{"min": 0, "max": 6, "points": 4}, {"min": 5, "max": None, "points": 0}]},
     "no gap or overlap"),
    ("kicker", {"fg_made_by_distance": [{"min": 40, "max": 39, "points": 3}]}, "below its min"),
])
def test_bad_kicker_or_dst_rules_are_rejected(tmp_path, section, bad, match):
    with pytest.raises(ValueError, match=match):
        load_rules(write_rules(tmp_path, {"name": "bad", "offense": {}, section: bad}))


def test_a_value_above_a_closed_last_tier_scores_nothing():
    rules = PRESETS["standard"]
    capped = ScoringRules("capped", rules.offense, dst={"points_allowed_tiers": [
        {"min": 0, "max": 0, "points": 5}, {"min": 1, "max": 20, "points": 1}]})
    assert score_dst({"points_allowed_all": 20}, capped) == 1
    assert score_dst({"points_allowed_all": 21}, capped) == 0


def test_scoring_rules_pickle_for_worker_processes():
    # Sprint 8: the rules go to each prediction worker; offense stays read-only.
    import pickle

    rules = PRESETS["sleeper"]
    copy = pickle.loads(pickle.dumps(rules))
    assert copy == rules and copy.offense["receptions"] == 1
    with pytest.raises(TypeError):
        copy.offense["receptions"] = 2
