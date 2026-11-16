"""Players and their next Game, from nflverse rosters and schedules.

An INPUT.md line is resolved to a canonical Player keyed on the nflverse
gsis_id; the Player's team then finds the next Game in the schedule.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from ffp.names import normalize_name

Row = Mapping[str, object]
Fetch = Callable[[int], Iterable[Row]]


@dataclass(frozen=True)
class Player:
    """A canonical NFL player."""

    gsis_id: str  # nflverse id, the key for everything else
    name: str  # nflverse display name, e.g. "Ja'Marr Chase"
    position: str  # nflverse position, e.g. "WR"
    team: str  # nflverse team code of the current team, e.g. "CIN"


@dataclass(frozen=True)
class RosterEntry:
    """One player's latest row in the nflverse weekly rosters."""

    gsis_id: str
    name: str
    position: str
    team: str
    status: str  # nflverse roster status, e.g. "ACT", "RES", "DEV"
    week: int  # the week this row is from


def load_roster(season: int, fetch: Fetch) -> list[RosterEntry]:
    """Each player's latest weekly-roster row in `season`."""
    latest: dict[str, RosterEntry] = {}
    for row in fetch(season):
        entry = RosterEntry(
            gsis_id=str(row["gsis_id"]),
            name=str(row["full_name"]),
            position=str(row["position"]),
            team=str(row["team"]),
            status=str(row["status"]),
            week=int(row["week"]),
        )
        held = latest.get(entry.gsis_id)
        if held is None or entry.week >= held.week:
            latest[entry.gsis_id] = entry
    return list(latest.values())


def resolve_player(text: str, roster: Sequence[RosterEntry]) -> Player:
    """The Player an INPUT.md line names."""
    key = normalize_name(text)
    for entry in roster:
        if normalize_name(entry.name) == key:
            return Player(entry.gsis_id, entry.name, entry.position, entry.team)
    raise ValueError(text)
