"""Player names -> Score Distributions: Prop Markets -> Fair Probabilities -> Stat Distributions -> simulation."""

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from ffp.distributions import FitError, StatDistribution, fit_receiving_yards
from ffp.fair import fair_points
from ffp.markets import Game, PropMarket
from ffp.names import normalize_name
from ffp.report import PlayerResult, SkippedPlayer, StatDetail, chances
from ffp.scoring import ScoringRules
from ffp.simulate import simulate_scores
from ffp.sources import draftkings

# Sprint 1 simplifications, reported on every player so the output can't be mistaken for the finished model.
SPRINT_1_POSITION = "WR"
SPRINT_1_FLAGS = (
    "position assumed WR (player resolution arrives in Sprint 2)",
    "receiving yards only (other Scoring Stats arrive in Sprint 3)",
)

NO_MARKETS = "no Prop Markets found for this player"


# A list marker ("- ", "* ", "1. ") with an optional task checkbox ("[ ] ", "[x] "), or a bare marker.
_LIST_MARKER = re.compile(r"^(?:[-*+]|\d+[.)])(?:\s+|$)(?:\[[ xX]\](?:\s+|$))?")
# A thematic break or front-matter fence: "---", "***", "___", "- - -", ...
_RULE = re.compile(r"^(?:([-*_])\s*){3,}$")
_EMPHASIS = re.compile(r"[*_`]")


def read_input(path: Path) -> list[str]:
    """Player names from INPUT.md, one per line.

    Blank lines, Markdown headings, horizontal rules and YAML front matter are
    ignored; list markers, task checkboxes and emphasis (**, _, `) are stripped,
    so a plain list and a Markdown list both work.
    """
    lines = [line.strip() for line in Path(path).read_text().splitlines()]
    if lines and lines[0] == "---" and "---" in lines[1:]:  # YAML front matter
        lines = lines[lines.index("---", 1) + 1 :]
    names = []
    for line in lines:
        if not line or line.startswith("#") or _RULE.match(line):
            continue
        names.append(_EMPHASIS.sub("", _LIST_MARKER.sub("", line)).strip())
    return [name for name in names if name]


def load_fixture_markets(fixtures: Path) -> tuple[list[PropMarket], dict[str, Game]]:
    """DraftKings receiving-yards Prop Markets and Games from recorded responses."""
    ladder = json.loads((Path(fixtures) / "dk_nfl_rec_yds_ladder.json").read_text())
    main_line = json.loads((Path(fixtures) / "dk_nfl_rec_yds_ou.json").read_text())
    markets = draftkings.parse_prop_markets(ladder, main_line, "receiving_yards")
    games = {g.game_id: g for g in draftkings.parse_games(ladder)}
    return markets, games


def predict(
    names: Sequence[str],
    markets: Sequence[PropMarket],
    games: dict[str, Game],
    rules: ScoringRules,
) -> tuple[list[PlayerResult], list[SkippedPlayer]]:
    """A Score Distribution summary for each named player who has Prop Markets."""
    by_player: dict[str, list[PropMarket]] = defaultdict(list)
    for market in markets:
        by_player[normalize_name(market.player)].append(market)

    results, skipped = [], []
    for name in names:
        player_markets = by_player.get(normalize_name(name))
        if not player_markets:
            skipped.append(SkippedPlayer(name, NO_MARKETS))
            continue
        # Only a fit failure skips the player; any other error is a bug or bad configuration and stops the run.
        try:
            receiving_yards = fit_receiving_yards(fair_points(player_markets), SPRINT_1_POSITION)
        except FitError as error:
            skipped.append(SkippedPlayer(name, f"could not fit receiving yards: {error}"))
            continue
        results.append(_player_result(name, player_markets, receiving_yards, games, rules))
    return results, skipped


def _player_result(
    name: str,
    markets: list[PropMarket],
    receiving_yards: StatDistribution,
    games: dict[str, Game],
    rules: ScoringRules,
) -> PlayerResult:
    scores = simulate_scores({"receiving_yards": receiving_yards}, rules)
    game = games.get(markets[0].game_id)
    return PlayerResult(
        player=name,
        team=markets[0].team or "",
        game=f"{game.away} @ {game.home}" if game else "",
        kickoff=game.kickoff if game else "",
        position=SPRINT_1_POSITION,
        percentiles=scores.percentiles(),
        chances=chances(scores),
        flags=list(SPRINT_1_FLAGS),
        stats={
            "receiving_yards": StatDetail(
                mean=float(receiving_yards.mean),
                p50=float(receiving_yards.ppf(0.5)),
                books=sorted({m.book for m in markets}),
                rungs=sum(len(m.ladder) for m in markets),
            )
        },
    )
