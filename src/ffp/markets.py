"""Prop Market types: what a sportsbook offers on one player's Scoring Stat in one Game."""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Rung:
    """One "threshold or more" price in a Ladder: the bet wins when Y >= threshold."""

    threshold: int
    odds: float  # American odds, vig included
    book: str


@dataclass(frozen=True)
class MainLine:
    """The two-sided over/under price. Over wins when Y > line."""

    line: float
    over_odds: float
    under_odds: float
    book: str

    @property
    def over_threshold(self) -> int:
        """The Over as a "threshold or more" event: Over 71.5 wins exactly when Y >= 72.

        Scoring Stats are whole numbers, so the Over and the Rung at this threshold
        are the same event.
        """
        return math.floor(self.line) + 1


def parse_utc(text: str) -> datetime:
    """A UTC datetime, to the second, from any ISO 8601 spelling the books, nflverse or a user give.

    Handles a trailing Z, any fraction of a second (DraftKings sends 7 digits,
    which Python before 3.14 can't read), and an explicit offset. A time with
    no offset is taken as UTC.
    """
    text = text.strip()
    utc = text.endswith(("Z", "z"))
    text = text[:-1] if utc else text
    text = re.sub(r"(T\d\d:\d\d:\d\d)\.\d+", r"\1", text)  # drop the fraction
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0)


def format_utc(moment: datetime) -> str:
    """The kickoff format used throughout: "2026-11-17T01:15:00Z"."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Game:
    """One scheduled NFL game, as a book lists it."""

    game_id: str  # the book's id
    away: str  # team name as the book spells it
    home: str
    kickoff: str  # ISO 8601 UTC
    book: str
    started: bool  # in progress or finished; such Games are not priced


@dataclass(frozen=True)
class PropMarket:
    """A book's offer on one player's Scoring Stat in one Game."""

    player: str  # name as the book spells it
    stat: str  # Scoring Stat key, e.g. "receiving_yards" (see ffp.scoring.SCORING_STATS)
    game_id: str  # the book's id for the Game
    book: str  # "draftkings" or "fanduel"
    main_line: MainLine | None
    ladder: tuple[Rung, ...]
    team: str | None = None  # the player's team as the book spells it, when known


def ladder_without_main_line_copy(ladder: list[Rung], main_line: MainLine | None) -> tuple[Rung, ...]:
    """The Ladder sorted by threshold, minus the Rung at the Main Line Over's threshold.

    "Over L" and the Rung "floor(L)+1 or more" are the same event. Books often
    copy the Over's odds onto that Rung (DK: O/U 71.5 at −113 → "72+" at −113)
    but not always (DK: Over 37.5 at −111 vs "38+" at −109; FD: Over 69.5 at −113
    vs "70+" at −114). The Main Line already carries the event, so the Rung is
    dropped either way.
    """
    if main_line is not None:
        same_event = main_line.over_threshold
        ladder = [r for r in ladder if r.threshold != same_event]
    return tuple(sorted(ladder, key=lambda r: r.threshold))


# Team-level Prop Markets (Sprint 6): their `player` is the team as the book spells it.
TEAM_POINTS = "team_points"  # a team's points in the Game: its team-total Main Line and alternate totals
DST_TDS = "dst_tds"  # TDs scored by a team's D/ST: the books' anytime/2+/3+ "D/ST" scorer prices


@dataclass(frozen=True)
class GameLine:
    """A book's main spread and total for one Game (Sprint 6). Either may be missing."""

    game_id: str  # the book's id
    book: str
    home_spread: float | None  # the home team's handicap: −3.5 when the home team is favoured by 3.5
    total: float | None  # the total points line
