"""Sleeper's player list (S11.2): Sleeper and ESPN ids for our Players, for the Leaderboard push.

The Leaderboard keys players by Sleeper id (PLAN, "Leaderboard site"), and its ESPN projection
fallback needs Sleeper id -> ESPN id. Sleeper's list (`PLAYERS_URL`, about 15 MB, free for
non-commercial use) carries both, but in November 2026 it had a gsis_id for only about a fifth of the
rostered QB/RB/WR/TE/K and an espn_id for about a quarter. nflverse's weekly rosters carry Sleeper's
and ESPN's ids for nearly all of them, so a Player's Sleeper id is, in order: the list's entry with his
gsis_id; the roster's sleeper_id, if the list has it; the one list entry with his name, team and a
fantasy position. His ESPN id is the list's, else the roster's.

The list is downloaded at most once a day and kept trimmed to the fields read here, under the day's
date (`load_players`).
"""

import json
import logging
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from ffp.players import DST, Player, RosterEntry, _id, name_key

log = logging.getLogger(__name__)

PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
DEFAULT_CACHE_DIR = Path(".cache") / "sleeper"  # gitignored with the rest of .cache/
POSITIONS = ("QB", "RB", "WR", "TE", "K")  # the Leaderboard's players; D/STs are keyed by team
# nflverse team code -> Sleeper's, where they differ (checked against Sleeper's list on 2026-11-20:
# every other code is the same, Washington included).
_SLEEPER_TEAMS = {"LA": "LAR"}


def team_code(nflverse_team: str) -> str:
    """Sleeper's code for an nflverse team code (the Leaderboard joins on Sleeper's)."""
    return _SLEEPER_TEAMS.get(nflverse_team, nflverse_team)


def dst_id(player: Player) -> str:
    """A D/ST's Sleeper id: its team, as Sleeper spells it."""
    if player.position != DST:
        raise ValueError(f"{player.name} is not a D/ST")
    return team_code(player.team)


@dataclass(frozen=True)
class SleeperPlayer:
    player_id: str
    name: str
    position: str
    team: str | None  # Sleeper's code; None for a free agent
    gsis_id: str | None
    espn_id: str | None


def _text(value) -> str | None:
    """A field as text, as `players._id` cleans an id column: stripped, 4429615.0 as "4429615", and
    None when missing (None, NaN, "", "None", "nan", "null")."""
    return _id(value)


_ESPN_ID = re.compile(r"(\d+)(?:\.0+)?")


def espn_id(value) -> str | None:
    """An ESPN id as digits ("4429615", also from 4429615.0 or "4429615.0"); None when it's missing or
    malformed (anything else is no use to the Leaderboard's ESPN fallback)."""
    match = _ESPN_ID.fullmatch(_text(value) or "")
    return match.group(1) if match else None


def parse_players(payload: Mapping) -> list[SleeperPlayer]:
    """The QB/RB/WR/TE/K in Sleeper's player list, ids cleaned (Sleeper pads some gsis_ids with a
    space and sends espn_id as a number). A player listed at another position counts at his first
    fantasy position among those (Travis Hunter is a DB with fantasy positions DB and WR; a FB's is RB)."""
    out = []
    for pid, p in payload.items():
        if not isinstance(p, Mapping):
            continue
        fantasy = p.get("fantasy_positions") if isinstance(p.get("fantasy_positions"), list) else []
        position = p.get("position") if p.get("position") in POSITIONS else next(
            (f for f in fantasy if f in POSITIONS), None)
        if position is None:
            continue
        name = _text(p.get("full_name")) or " ".join(filter(None, (_text(p.get("first_name")), _text(p.get("last_name")))))
        out.append(SleeperPlayer(str(p.get("player_id") or pid), name, position, _text(p.get("team")),
                                 _text(p.get("gsis_id")), _text(p.get("espn_id"))))
    return out


def _dated(cache_dir: Path, day: date) -> Path:
    return cache_dir / f"players-{day.isoformat()}.json"


def load_players(get_json: Callable[[str], Mapping], cache_dir: Path = DEFAULT_CACHE_DIR,
                 today: date | None = None) -> list[SleeperPlayer]:
    """Sleeper's QB/RB/WR/TE/K: today's saved list, else downloaded with `get_json` (an Http's) and
    saved trimmed under today's date, older days' lists removed. A failed download falls back to the
    newest saved list, with a warning; with none saved, its error is raised."""
    cache_dir, today = Path(cache_dir), today or date.today()
    path = _dated(cache_dir, today)
    if path.exists():
        return _read(path)
    try:
        players = parse_players(get_json(PLAYERS_URL))
    except Exception as error:  # noqa: BLE001 - any failure: an older list is better than none
        older = sorted(cache_dir.glob("players-*.json"))
        if not older:
            raise
        log.warning("Sleeper's player list could not be downloaded (%s: %s); using the one from %s",
                    type(error).__name__, error, older[-1].stem.removeprefix("players-"))
        return _read(older[-1])
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps([asdict(p) for p in players], separators=(",", ":")))
    tmp.replace(path)
    for old in cache_dir.glob("players-*.json"):
        if old != path:
            old.unlink(missing_ok=True)
    return players


def _read(path: Path) -> list[SleeperPlayer]:
    return [SleeperPlayer(**row) for row in json.loads(path.read_text())]


class SleeperIds:
    """Sleeper and ESPN ids for roster entries, from Sleeper's list (see the module docstring)."""

    def __init__(self, players: Iterable[SleeperPlayer]):
        self.players = list(players)
        self._by_id = {p.player_id: p for p in self.players}
        self._by_gsis: dict[str, SleeperPlayer] = {}
        for p in self.players:
            if p.gsis_id:
                self._by_gsis.setdefault(p.gsis_id, p)
        self._by_name: dict[tuple[str, str], list[SleeperPlayer]] = {}
        for p in self.players:
            if p.team:
                self._by_name.setdefault((name_key(p.name), p.team), []).append(p)

    def find(self, entry: RosterEntry) -> SleeperPlayer | None:
        """The list's entry for a roster entry, or None."""
        found = self._by_gsis.get(entry.gsis_id)
        if found is None and entry.sleeper_id:
            found = self._by_id.get(entry.sleeper_id)
        if found is None:
            same = self._by_name.get((name_key(entry.name), team_code(entry.team)), [])
            if len(same) > 1:
                same = [p for p in same if p.position == entry.position]
            found = same[0] if len(same) == 1 else None
        return found

    def lookup(self, entry: RosterEntry) -> tuple[str | None, str | None]:
        """(Sleeper id, ESPN id) for a roster entry: the ESPN id is the list's, else the roster's, each
        kept only if it's digits (`espn_id`)."""
        found = self.find(entry)
        if found is None:
            return None, None
        return found.player_id, espn_id(found.espn_id) or espn_id(entry.espn_id)

    def id_map(self, teams: Iterable[str], roster: Sequence[RosterEntry]) -> dict[str, str]:
        """Sleeper id -> ESPN id for every QB/RB/WR/TE/K the list has on `teams` (nflverse codes), priced
        or not. The ESPN id is the list's, else that of the roster entry with his Sleeper id or gsis_id."""
        wanted = {team_code(t) for t in teams}
        by_sleeper = {e.sleeper_id: e for e in roster if e.sleeper_id}
        by_gsis = {e.gsis_id: e for e in roster}
        out = {}
        for p in self.players:
            if p.team not in wanted:
                continue
            espn = espn_id(p.espn_id)
            if espn is None:
                entry = by_sleeper.get(p.player_id) or (by_gsis.get(p.gsis_id) if p.gsis_id else None)
                espn = espn_id(entry.espn_id) if entry is not None else None
            if espn is not None and p.player_id.isdigit():
                out[p.player_id] = espn
        return out
