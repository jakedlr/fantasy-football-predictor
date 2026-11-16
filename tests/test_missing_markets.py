"""S3.5 groundwork: a player missing a market simply has no Prop Market for that stat.

The integrator turns "no Prop Markets for receptions" into "incomplete: missing receptions".
"""

import json
from pathlib import Path

from ffp.sources import draftkings, fanduel

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def players(markets) -> set[str]:
    return {m.player for m in markets}


def receptions_players() -> set[str]:
    dk = draftkings.parse_prop_markets(
        load("dk_nfl_receptions_ladder.json"), load("dk_nfl_receptions_ou.json"), "receptions"
    )
    fd = fanduel.parse_prop_markets(load("fd_nfl_event_receiving_tab.json"), "receptions")
    return players(dk) | players(fd)


def td_players() -> set[str]:
    dk = draftkings.parse_prop_markets(load("dk_nfl_td_scorer.json"), None, "rush_rec_tds")
    fd = fanduel.parse_prop_markets(load("fd_nfl_event_td_scorer_tab.json"), "rush_rec_tds")
    return players(dk) | players(fd)


def test_a_te_with_td_prices_but_no_receptions_market_has_no_receptions_prop_markets():
    # David Njoku (LAC TE) is priced to score at both books but has no receptions market.
    assert "David Njoku" in td_players()
    assert "David Njoku" not in receptions_players()


def test_every_receptions_player_also_has_td_prop_markets():
    assert receptions_players() <= td_players()


def test_a_tab_that_is_not_posted_gives_no_receptions_or_td_prop_markets():
    tab = load("fd_nfl_event_receiving_tab_not_posted.json")
    assert fanduel.parse_prop_markets(tab, "receptions") == []
    assert fanduel.parse_prop_markets(tab, "rush_rec_tds") == []
