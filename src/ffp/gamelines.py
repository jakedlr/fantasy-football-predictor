"""Game Lines → Implied Team Totals and each team's markets (Sprint 6, kickers and D/ST).

A team's Implied Team Total is half the total plus half its expected margin: with the home team
favoured by s (its handicap −s) and total t, home gets (t + s)/2 and away (t − s)/2. Each Book's
main spread and total give one; the Books are averaged. With no Book line, nflverse's schedule
lines are used, and the result says so.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ffp.markets import GameLine, PropMarket
from ffp.players import ScheduledGame, UnknownTeamError, team_code_from_book_name

NFLVERSE = "nflverse"  # the source named when the schedule's lines stand in for the Books'


@dataclass(frozen=True)
class ImpliedTotals:
    """Both teams' Implied Team Totals in one Game, and where they came from."""

    by_team: dict[str, float]  # nflverse team code -> Implied Team Total
    sources: list[str]  # the Books whose lines were averaged, or [NFLVERSE]

    def expected_margin(self, team: str) -> float:
        """How many points `team` is favoured by (negative: the underdog)."""
        (opp,) = [t for t in self.by_team if t != team]
        return self.by_team[team] - self.by_team[opp]


def _home_totals(spread: float, total: float) -> tuple[float, float]:
    """(home, away) Implied Team Totals from the home team's handicap and the total."""
    return (total - spread) / 2, (total + spread) / 2


def implied_totals(game: ScheduledGame, lines: Iterable[tuple[str, str, GameLine]]) -> ImpliedTotals | None:
    """The Game's Implied Team Totals from Book lines given as (book home team, book away team,
    GameLine) for this Game, else from nflverse's schedule lines; None with neither.

    A Book that lists the teams the other way round from nflverse (a neutral site) has its
    spread read from the right side."""
    home, away, books = [], [], []
    for book_home, book_away, line in lines:
        if line.home_spread is None or line.total is None:
            continue
        try:
            flipped = team_code_from_book_name(book_home) != game.home
        except UnknownTeamError:
            continue
        h, a = _home_totals(line.home_spread, line.total)
        home.append(a if flipped else h)
        away.append(h if flipped else a)
        books.append(line.book)
    if books:
        return ImpliedTotals({game.home: sum(home) / len(home), game.away: sum(away) / len(away)},
                             sorted(set(books)))
    if game.spread_line is not None and game.total_line is not None:
        h, a = _home_totals(-game.spread_line, game.total_line)
        return ImpliedTotals({game.home: h, game.away: a}, [NFLVERSE])
    return None


def team_markets(markets: Sequence[PropMarket], team: str, stat: str) -> list[PropMarket]:
    """The `stat` markets (TEAM_POINTS, DST_TDS) whose team is `team` (an nflverse code)."""
    out = []
    for market in markets:
        if market.stat != stat:
            continue
        try:
            if team_code_from_book_name(market.player) == team:
                out.append(market)
        except UnknownTeamError:
            continue
    return out
