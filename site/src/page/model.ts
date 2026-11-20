/**
 * The Leaderboard's pure logic: search, position filter, Game filter, ranking, sorting and the
 * small labels the table shows. No DOM, no clock. Terms as in GLOSSARY.md.
 */
import type { Game, Leaderboard, Player, PointsEntry } from "./types.js";

export const POSITION_FILTERS = ["All", "QB", "RB", "WR", "TE", "FLEX", "K", "D/ST"] as const;
export type PositionFilter = (typeof POSITION_FILTERS)[number];
const FLEX = new Set(["RB", "WR", "TE"]);

export type SortKey = "rank" | "name" | "team" | "pos" | "opp" | "status" | "week" | "soFar";
export type SortDir = "asc" | "desc";
/** The direction a column sorts in when its header is first chosen. */
export const DEFAULT_DIR: Record<SortKey, SortDir> = {
  rank: "asc", name: "asc", team: "asc", pos: "asc", opp: "asc", status: "asc", week: "desc", soFar: "desc",
};

export interface ViewState {
  pos: PositionFilter;
  q: string;
  /** Scoring Rules name. */
  rule: string;
  /** One Game's id, or null for every Game. */
  game: string | null;
  sort: SortKey;
  dir: SortDir;
}

/** A player with his Game and his search text, built once per fetch. */
export interface Entry {
  player: Player;
  game: Game;
  words: string[];
}

export interface Row {
  player: Player;
  game: Game;
  points: PointsEntry;
  /** By Week Points within the current filter; ties share a rank. */
  rank: number;
}

/** City and nickname per team, for search ("Ravens", "Baltimore"). */
export const TEAM_NAMES: Record<string, string> = {
  ARI: "Arizona Cardinals", ATL: "Atlanta Falcons", BAL: "Baltimore Ravens", BUF: "Buffalo Bills",
  CAR: "Carolina Panthers", CHI: "Chicago Bears", CIN: "Cincinnati Bengals", CLE: "Cleveland Browns",
  DAL: "Dallas Cowboys", DEN: "Denver Broncos", DET: "Detroit Lions", GB: "Green Bay Packers",
  HOU: "Houston Texans", IND: "Indianapolis Colts", JAX: "Jacksonville Jaguars", KC: "Kansas City Chiefs",
  LAC: "Los Angeles Chargers", LAR: "Los Angeles Rams", LV: "Las Vegas Raiders", MIA: "Miami Dolphins",
  MIN: "Minnesota Vikings", NE: "New England Patriots", NO: "New Orleans Saints", NYG: "New York Giants",
  NYJ: "New York Jets", PHI: "Philadelphia Eagles", PIT: "Pittsburgh Steelers", SEA: "Seattle Seahawks",
  SF: "San Francisco 49ers", TB: "Tampa Bay Buccaneers", TEN: "Tennessee Titans", WAS: "Washington Commanders",
};

/** Lower case, accents and joining punctuation dropped, hyphens and slashes as spaces' neighbours. */
export function normalize(s: string): string {
  return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/[.'’/]/g, "").replace(/[-_,]+/g, " ").trim();
}

const words = (s: string) => normalize(s).split(/\s+/).filter(Boolean);

export function buildIndex(board: Pick<Leaderboard, "games" | "players">): Entry[] {
  const games = new Map(board.games.map((g) => [g.id, g]));
  const entries: Entry[] = [];
  for (const player of board.players) {
    const game = games.get(player.gameId);
    if (!game) continue; // a player whose Game is missing can't be placed; the schema doesn't allow it
    entries.push({ player, game, words: words(`${player.name} ${player.team} ${TEAM_NAMES[player.team] ?? ""}`) });
  }
  return entries;
}

/** Position, Game and search; every typed word must start a word of the name, team or team name. */
export function filterEntries(entries: Entry[], view: Pick<ViewState, "pos" | "q" | "game">): Entry[] {
  const terms = words(view.q);
  const { pos, game } = view;
  return entries.filter((e) => {
    const p = e.player.position;
    if (pos === "FLEX" ? !FLEX.has(p) : pos !== "All" && p !== pos) return false;
    if (game !== null && e.player.gameId !== game) return false;
    return terms.every((t) => e.words.some((w) => w.startsWith(t)));
  });
}

const soFarOrLow = (x: number | null) => x ?? -Infinity;

/** Ranks by Week Points (ties: Points So Far, then name), standard competition ranking (1, 1, 3). */
export function rankRows(entries: Entry[], rule: string): Row[] {
  const rows = entries.map((e) => ({ player: e.player, game: e.game, points: pointsFor(e.player, rule), rank: 0 }));
  rows.sort((a, b) => b.points.week - a.points.week
    || soFarOrLow(b.points.soFar) - soFarOrLow(a.points.soFar)
    || a.player.name.localeCompare(b.player.name));
  rows.forEach((r, i) => {
    const prev = rows[i - 1];
    r.rank = prev && prev.points.week === r.points.week ? prev.rank : i + 1;
  });
  return rows;
}

function pointsFor(player: Player, rule: string): PointsEntry {
  return player.points[rule] ?? Object.values(player.points)[0] ?? { week: 0, soFar: null, expected: null };
}

const STATUS_ORDER = { live: 0, upcoming: 1, final: 2 } as const;
type Key = (r: Row) => string | number | null;
const KEYS: Record<SortKey, Key> = {
  rank: (r) => r.rank,
  name: (r) => r.player.name,
  team: (r) => r.player.team,
  pos: (r) => r.player.position,
  opp: (r) => opponentOf(r.player.team, r.game),
  status: (r) => STATUS_ORDER[r.game.status] * 1e13 + (r.game.status === "upcoming" ? Date.parse(r.game.kickoff) : 0),
  week: (r) => r.points.week,
  soFar: (r) => r.points.soFar,
};

/** Sorts by one column; missing values go last in either direction; ties keep the rank order. */
export function sortRows(rows: Row[], key: SortKey, dir: SortDir): Row[] {
  const get = KEYS[key];
  const m = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = get(a);
    const y = get(b);
    if (x === y) return a.rank - b.rank || a.player.name.localeCompare(b.player.name);
    if (x === null) return 1;
    if (y === null) return -1;
    return (typeof x === "string" ? x.localeCompare(y as string) : x - (y as number)) * m;
  });
}

export function query(entries: Entry[], view: ViewState): Row[] {
  return sortRows(rankRows(filterEntries(entries, view), view.rule), view.sort, view.dir);
}

function opponentOf(team: string, game: Pick<Game, "home" | "away">): string {
  return game.home === team ? game.away : game.home;
}

/** "vs CIN" at home, "@ BAL" away. */
export function opponentText(team: string, game: Pick<Game, "home" | "away">): string {
  return game.home === team ? `vs ${game.away}` : `@ ${game.home}`;
}

/** How much of Week Points is already Points So Far: 1 when Final, 0 when Upcoming. */
export function bankedFraction(points: PointsEntry, game: Pick<Game, "status">): number {
  if (game.status === "final") return 1;
  if (game.status === "upcoming" || points.soFar === null || points.week <= 0) return 0;
  return Math.min(1, Math.max(0, points.soFar / points.week));
}

/** Whether ESPN's projection still counts toward this player's Week Points. */
export function showEspnTag(player: Pick<Player, "expectedSource">, game: Pick<Game, "status">): boolean {
  return player.expectedSource === "espn" && game.status !== "final";
}

/** One decimal, a real minus sign, an em dash when missing. */
export function formatPoints(x: number | null): string {
  if (x === null) return "—";
  const s = (Math.abs(x) < 0.05 ? 0 : x).toFixed(1);
  return s.startsWith("-") ? `−${s.slice(1)}` : s;
}

const RULE_LABELS: Record<string, string> = { ppr: "PPR", "half-ppr": "Half PPR", standard: "Standard", "espn-league": "ESPN league" };
export function ruleLabel(name: string): string {
  const known = RULE_LABELS[name];
  if (known) return known;
  const s = name.replace(/-/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
