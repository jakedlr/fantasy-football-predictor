"""What players actually did: each player-game's Scoring Stats from nflverse, for a Backtest.

Rows come from nflverse weekly player stats (`nflreadpy.load_player_stats`,
`summary_level="week"`), which carry the nflverse `game_id` and the player's
gsis_id as `player_id`.
"""

from collections.abc import Callable, Iterable, Mapping

from ffp import weekly
from ffp.scoring import SCORING_STATS, ScoringRules, score

Row = Mapping[str, object]
Fetch = Callable[[int], Iterable[Row]]
Outcomes = dict[tuple[str, str], dict[str, float]]

# The stats each outcome holds: every Scoring Stat, plus carries (a Usage Stat).
OUTCOME_STATS = (*SCORING_STATS, "carries")

# Each outcome stat as the sum of these nflverse columns.
COLUMNS: dict[str, tuple[str, ...]] = {
    "passing_yards": ("passing_yards",),
    "passing_tds": ("passing_tds",),
    "passing_interceptions": ("passing_interceptions",),
    "passing_2pt": ("passing_2pt_conversions",),
    "rushing_yards": ("rushing_yards",),
    "rushing_tds": ("rushing_tds",),
    "rushing_2pt": ("rushing_2pt_conversions",),
    "receptions": ("receptions",),
    "receiving_yards": ("receiving_yards",),
    "receiving_tds": ("receiving_tds",),
    "receiving_2pt": ("receiving_2pt_conversions",),
    # Not fumbles_lost_total: that also counts return fumbles, which Sleeper
    # (and so Kalshi) doesn't charge (research/kalshi-fantasy-points.md §5).
    "fumbles_lost": ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"),
    "carries": ("carries",),
}


def load_outcomes(seasons: int | Iterable[int], fetch: Fetch | None = None) -> Outcomes:
    """Each player-game's actual stats, keyed by (nflverse game_id, gsis_id).

    The value maps every name in OUTCOME_STATS to a number; a missing or empty
    column counts as 0. `fetch(season)` returns nflverse weekly player-stats rows;
    it defaults to nflreadpy (network, cached by nflreadpy). Rows of every
    season type (REG, POST) are kept. Rows with no player or game id are skipped.
    """
    if isinstance(seasons, int):
        seasons = [seasons]
    outcomes: Outcomes = {}
    for season in seasons:
        for row in (fetch or weekly.nflreadpy_player_stats)(season):
            game_id, gsis_id = row.get("game_id"), row.get("player_id")
            if not game_id or not gsis_id:
                continue
            outcomes[str(game_id), str(gsis_id)] = {
                stat: sum(weekly.number(row.get(column)) for column in columns) for stat, columns in COLUMNS.items()
            }
    return outcomes


def fantasy_points(stats: Mapping[str, float], rules: ScoringRules) -> float:
    """Fantasy points for an outcome, rounded to 2 decimals like Sleeper's published totals.

    The rounding keeps a total like 5.48 from coming out as 5.4799999..., which
    matters against a strictly-greater line such as Kalshi's "above 5.48".
    """
    return round(float(score(stats, rules)), 2)
