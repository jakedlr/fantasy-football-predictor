"""Players and their next Game, from nflverse rosters and schedules.

An INPUT.md line is resolved to a canonical Player keyed on the nflverse
gsis_id; the Player's team then finds the next Game in the schedule.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

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


class UnsupportedPlayerError(PlayerError):
    """A K, a D/ST or another position outside QB/RB/WR/TE."""


# Names books or people use that nflverse doesn't, as normalized name -> the
# normalized nflverse display name. Suffixes ("Kenneth Walker" vs "Kenneth
# Walker III") need no entry: normalize_name drops them.
ALIASES: dict[str, str] = {
    "hollywood brown": "marquise brown",
    "chigoziem okonkwo": "chig okonkwo",
    "zonovan knight": "bam knight",
    "gabriel davis": "gabe davis",
    "nathaniel dell": "tank dell",
    "joshua palmer": "josh palmer",
    "joshua downs": "josh downs",
}


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


def load_roster(season: int, fetch: Fetch | None = None) -> list[RosterEntry]:
    """Each player's latest weekly-roster row in `season`, all positions.

    `fetch(season)` returns nflverse `load_rosters_weekly` rows; it defaults
    to nflreadpy (network, cached by nflreadpy). The latest
    week gives the current team, so traded players resolve to their new team.
    Players whose latest status is CUT or RET are dropped: they're on no team.
    Every other status stays (ACT, RES, INA, DEV, ...), so a player on reserve
    still resolves and is later skipped for having no Prop Markets. A team on
    bye has no row in its bye week, which is why "latest" is per player and
    not the season's last week. Non-skill positions are kept so a K or a
    defender gets a precise error instead of "unknown".
    """
    latest: dict[str, RosterEntry] = {}
    for row in (fetch or _nflreadpy_rosters_weekly)(season):
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
    return [entry for entry in latest.values() if entry.status not in _OFF_ROSTER]


_OFF_ROSTER = frozenset({"CUT", "RET"})


def resolve_player(text: str, roster: Sequence[RosterEntry]) -> Player:
    """The Player an INPUT.md line names.

    `text` is a player name, optionally followed by a team hint: "Name (TEAM)".
    Names compare by `normalize_name` after the alias table; only QB/RB/WR/TE
    count. The hint keeps only the players on that team. A K, a D/ST ("Ravens
    D/ST", "BAL DST", "Ravens") or any other position raises
    UnsupportedPlayerError.
    """
    name, hint = _split_team_hint(text)
    if _names_a_dst(name):
        raise UnsupportedPlayerError(
            f'"{text}" looks like a team defense: D/ST is not supported yet, only QB/RB/WR/TE'
        )
    team = _hint_team_code(hint) if hint is not None else None
    key = normalize_name(name)
    key = ALIASES.get(key, key)
    same_name = [entry for entry in roster if normalize_name(entry.name) == key]
    matches = [entry for entry in same_name if entry.position in SUPPORTED_POSITIONS]
    if not matches and same_name:
        raise UnsupportedPlayerError(_unsupported_message(text, same_name))
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


def _unsupported_message(text: str, entries: Sequence[RosterEntry]) -> str:
    found = ", ".join(f"{e.name} ({e.position}, {e.team})" for e in entries)
    if any(e.position == "K" for e in entries):
        return f'"{text}" is a kicker ({found}): K is not supported yet, only QB/RB/WR/TE'
    return f'"{text}" is not a QB/RB/WR/TE ({found}): only QB/RB/WR/TE are supported'


_DST = re.compile(r"^(?:(?P<team>.*)\s)?(?:dst|d st|def|defense|defence)$")


def _names_a_dst(name: str) -> bool:
    """Whether a name is a team defense: "Ravens D/ST", "BAL DST", "Ravens", "D/ST"."""
    if _DST.match(normalize_name(name)):
        return True
    try:
        team_code_from_book_name(name)
    except UnknownTeamError:
        return False
    return True


def _hint_team_code(hint: str) -> str:
    try:
        return team_code_from_book_name(hint)
    except UnknownTeamError:
        raise UnknownPlayerError(
            f'"({hint})" is not an NFL team; use a team code such as (BAL)'
        ) from None


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


# Next Game

PROPS_NOT_POSTED = "props not posted yet for this Game; rerun closer to kickoff"

_EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ScheduledGame:
    """One Game in the nflverse schedule. Teams are nflverse team codes."""

    game_id: str  # nflverse id, "{season}_{week:02}_{away}_{home}"
    season: int
    week: int
    home: str
    away: str
    kickoff: str  # ISO 8601 UTC, e.g. "2026-11-17T01:15:00Z", like markets.Game.kickoff
    home_score: int | None = None  # None until the Game is final
    away_score: int | None = None

    @property
    def scored(self) -> bool:
        return self.home_score is not None or self.away_score is not None

    def opponent(self, team: str) -> str:
        """The other team in this Game."""
        if team == self.home:
            return self.away
        if team == self.away:
            return self.home
        raise ValueError(f"{team} does not play in {self.game_id}")


def kickoff_utc(gameday: str, gametime: str) -> str:
    """nflverse `gameday` ("2026-11-16") + `gametime` ("20:15"), US Eastern, as ISO UTC."""
    local = datetime.fromisoformat(f"{gameday}T{gametime}").replace(tzinfo=_EASTERN)
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_schedule(season: int, fetch: Fetch | None = None) -> list[ScheduledGame]:
    """Every Game in `season` from nflverse `load_schedules` rows, in kickoff order.

    `fetch(season)` defaults to nflreadpy, like `load_roster`.

    A Game with no `gametime` yet (rare; flexed Games get one weeks ahead)
    is placed at 13:00 Eastern, the usual Sunday slot.
    """
    games = [
        ScheduledGame(
            game_id=str(row["game_id"]),
            season=int(row["season"]),
            week=int(row["week"]),
            home=str(row["home_team"]),
            away=str(row["away_team"]),
            kickoff=kickoff_utc(str(row["gameday"]), str(row["gametime"] or "13:00")),
            home_score=_score(row["home_score"]),
            away_score=_score(row["away_score"]),
        )
        for row in (fetch or _nflreadpy_schedules)(season)
    ]
    return sorted(games, key=lambda game: game.kickoff)


def _score(value: object) -> int | None:
    """A score from a polars row (int or None) or a CSV row ("38" or "")."""
    if value is None or value == "":
        return None
    return int(value)


def next_game(team: str, schedule: Iterable[ScheduledGame], now: datetime) -> ScheduledGame | None:
    """`team`'s next Game: no score yet and kickoff after `now`. None if no Game is left.

    A Game that has kicked off but has no score yet is in progress, not next.
    `now` must be timezone-aware.
    """
    if now.tzinfo is None:
        raise ValueError("now must carry a timezone, e.g. datetime.now(UTC)")
    remaining = [
        game
        for game in schedule
        if team in (game.home, game.away)
        and not game.scored
        and datetime.fromisoformat(game.kickoff) > now
    ]
    return min(remaining, key=lambda game: game.kickoff, default=None)


def _nflreadpy_rosters_weekly(season: int) -> list[dict]:
    import nflreadpy  # imported here so tests and offline runs never need it

    return nflreadpy.load_rosters_weekly(season).to_dicts()


def _nflreadpy_schedules(season: int) -> list[dict]:
    import nflreadpy

    return nflreadpy.load_schedules(season).to_dicts()
