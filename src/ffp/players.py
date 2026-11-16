"""Players and their next Game, from nflverse rosters and schedules.

An INPUT.md line is resolved to a canonical Player keyed on the nflverse
gsis_id; the Player's team then finds the next Game in the schedule.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from ffp.names import normalize_name

Row = Mapping[str, object]
Fetch = Callable[[int], Iterable[Row]]

SUPPORTED_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})


class PlayerError(ValueError):
    """An INPUT.md line that can't be resolved to one supported Player."""


class UnknownPlayerError(PlayerError):
    """No supported player has this name."""


class AmbiguousPlayerError(PlayerError):
    """More than one supported player has this name; a (TEAM) hint is needed."""


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
    """The Player an INPUT.md line names.

    `text` is a player name, optionally followed by a team hint: "Name (TEAM)".
    Names compare by `normalize_name`; only QB/RB/WR/TE count. The hint keeps
    only the players on that team.
    """
    name, team = _split_team_hint(text)
    key = normalize_name(name)
    matches = [
        entry
        for entry in roster
        if entry.position in SUPPORTED_POSITIONS and normalize_name(entry.name) == key
    ]
    if team is not None:
        on_team = [entry for entry in matches if entry.team == team]
        if not on_team and matches:
            found = ", ".join(f"{e.name} ({e.position}, {e.team})" for e in matches)
            raise UnknownPlayerError(f'no "{name}" on {team}; found {found}')
        matches = on_team
    if not matches:
        raise UnknownPlayerError(f'no QB/RB/WR/TE named "{name}" on a current NFL roster')
    if len(matches) > 1:
        candidates = ", ".join(f"{e.name} ({e.position}, {e.team})" for e in matches)
        raise AmbiguousPlayerError(
            f'"{text}" matches more than one player: {candidates}. '
            f'Add the team to the line, e.g. "{text} ({matches[0].team})", '
            "using Name (TEAM)."
        )
    entry = matches[0]
    return Player(entry.gsis_id, entry.name, entry.position, entry.team)


_TEAM_HINT = re.compile(r"^(?P<name>.*?)\s*\(\s*(?P<team>[^()]*?)\s*\)\s*$")


def _split_team_hint(text: str) -> tuple[str, str | None]:
    """("Name", "TEAM") from "Name (TEAM)"; ("Name", None) without a hint."""
    match = _TEAM_HINT.match(text.strip())
    if match is None:
        return text.strip(), None
    return match["name"], match["team"].upper()
