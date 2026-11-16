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


# nflverse team code -> (city as FanDuel spells it, nickname). DraftKings spells a
# team "<short city> <nickname>", e.g. "LA Chargers", "NY Jets", "BAL Ravens".
TEAMS: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"),
    "LA": ("Los Angeles", "Rams"),
    "LAC": ("Los Angeles", "Chargers"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WAS": ("Washington", "Commanders"),
}

# Abbreviations other sites use for the same teams.
_OTHER_CODES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "LVR": "LV"}

# City prefixes a book may share between two teams ("LA Rams", "NY Jets").
_SHARED_PREFIXES = {"LA": {"LA", "LAC"}, "NY": {"NYG", "NYJ"}}

_BY_NICKNAME = {nickname.lower(): code for code, (_, nickname) in TEAMS.items()}


class UnknownTeamError(ValueError):
    """A team name or code that isn't one of the 32 NFL teams."""


def _team_code_from_word(word: str) -> str | None:
    upper = word.upper()
    if upper in TEAMS:
        return upper
    if upper in _OTHER_CODES:
        return _OTHER_CODES[upper]
    return _BY_NICKNAME.get(word.lower())


def team_code_from_book_name(name: str) -> str:
    """The nflverse team code for a team as a book or a person spells it.

    Accepts a code ("BAL", also "LAR"/"JAC"/"WSH"/"ARZ"/"LVR"), a nickname
    ("Ravens"), DraftKings' "BAL Ravens" / "LA Chargers" and FanDuel's
    "Baltimore Ravens" / "Los Angeles Chargers". Note nflverse's Rams are "LA".
    """
    words = name.split()
    if len(words) == 1:
        code = _team_code_from_word(words[0])
        if code is not None:
            return code
    elif len(words) > 1:
        code = _BY_NICKNAME.get(words[-1].lower())
        prefix = " ".join(words[:-1])
        if code is not None and _prefix_fits(prefix, code):
            return code
    raise UnknownTeamError(f'"{name}" is not an NFL team')


def _prefix_fits(prefix: str, code: str) -> bool:
    """Whether the words before the nickname name the same team."""
    upper = prefix.upper()
    if code in _SHARED_PREFIXES.get(upper, ()):
        return True
    if _OTHER_CODES.get(upper, upper) == code:
        return True
    return prefix.lower() == TEAMS[code][0].lower()


_TEAM_HINT = re.compile(r"^(?P<name>.*?)\s*\(\s*(?P<team>[^()]*?)\s*\)\s*$")


def _split_team_hint(text: str) -> tuple[str, str | None]:
    """("Name", "TEAM") from "Name (TEAM)"; ("Name", None) without a hint."""
    match = _TEAM_HINT.match(text.strip())
    if match is None:
        return text.strip(), None
    return match["name"], match["team"].upper()
