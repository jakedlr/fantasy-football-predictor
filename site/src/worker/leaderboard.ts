/**
 * Scoreboard + Sleeper players → the Leaderboard JSON (site/schema/leaderboard.schema.json).
 * Pure: no fetch, no KV, no clock.
 *
 * Sprint 10 has Points So Far only: `expected` and `expectedSource` are null, Week Points equal
 * Points So Far for Live and Final Games, and are 0 (with Points So Far null) for Upcoming Games.
 * Sprint 11 adds Expected Points and the Projected Total here.
 */
import { LEADERBOARD_HEAD, updatedAtOf } from "./kv.js";
import type { Game, Scoreboard, Week } from "./scoreboard.js";
import { RULES, RULES_NAMES, scoreLine } from "./scoring.js";
import type { SleeperPlayer } from "./sleeper.js";

export interface PointsEntry {
  /** Week Points: the ranking number. */
  week: number;
  /** Points So Far; null when Upcoming. */
  soFar: number | null;
  /** Expected Points; null until Sprint 11. */
  expected: number | null;
}

export interface LeaderboardPlayer {
  id: string;
  name: string;
  team: string;
  position: "QB" | "RB" | "WR" | "TE" | "K" | "D/ST";
  /** ESPN event id; the page finds the opponent through it. */
  gameId: string;
  out: boolean;
  expectedSource: "ffp" | "espn" | null;
  /** Keyed by Scoring Rules name, in `scoringRules` order. */
  points: Record<string, PointsEntry>;
}

export interface Leaderboard {
  schemaVersion: 1;
  updatedAt: string;
  expectedPointsAsOf: string | null;
  week: Week;
  scoringRules: string[];
  games: Game[];
  players: LeaderboardPlayer[];
}

/** Points to 2 decimals, which also clears float noise (0.1 × 3) and "-0". */
export function round2(x: number): number {
  return Math.round(x * 100) / 100 || 0;
}

const UPCOMING: PointsEntry = Object.freeze({ week: 0, soFar: null, expected: null }) as PointsEntry;

/**
 * One Leaderboard player per Sleeper player whose team plays this NFL Week, joined to his Game by
 * team; a team with no Game (bye, or a canceled Game) is left out. Players are ordered by id, so
 * the same stats always serialize to the same text whatever order Sleeper sends its rows in.
 */
export function buildLeaderboard(scoreboard: Scoreboard, sleeperPlayers: readonly SleeperPlayer[], updatedAt: string): Leaderboard {
  const gameByTeam = new Map<string, Game>();
  for (const g of scoreboard.games) {
    gameByTeam.set(g.home, g);
    gameByTeam.set(g.away, g);
  }
  const rules = RULES_NAMES.map((name) => [name, RULES[name]!] as const);

  const players: LeaderboardPlayer[] = [];
  for (const p of sleeperPlayers) {
    const game = gameByTeam.get(p.team);
    if (game === undefined) continue;
    const points: Record<string, PointsEntry> = {};
    for (const [name, r] of rules) {
      if (game.status === "upcoming") {
        points[name] = { ...UPCOMING };
      } else {
        const soFar = round2(scoreLine(p, r));
        points[name] = { week: soFar, soFar, expected: null };
      }
    }
    players.push({ id: p.id, name: p.name, team: p.team, position: p.position, gameId: game.id, out: false, expectedSource: null, points });
  }
  players.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  return {
    schemaVersion: 1,
    updatedAt,
    expectedPointsAsOf: null,
    week: scoreboard.week,
    scoringRules: [...RULES_NAMES],
    games: scoreboard.games,
    players,
  };
}

/**
 * The Leaderboard as KV stores it and /api/leaderboard serves it: compact JSON, beginning with
 * schemaVersion then updatedAt (kv.ts LEADERBOARD_HEAD).
 */
export function serialize(lb: Leaderboard): string {
  const { schemaVersion, updatedAt, ...rest } = lb;
  return JSON.stringify({ schemaVersion, updatedAt, ...rest });
}

export { updatedAtOf };

/** Whether two serialized Leaderboards differ in nothing but updatedAt. One string compare, no parse. */
export function sameContent(a: string, b: string): boolean {
  const ha = LEADERBOARD_HEAD.exec(a);
  const hb = LEADERBOARD_HEAD.exec(b);
  if (!ha || !hb) return false;
  return a.length - ha[0].length === b.length - hb[0].length && a.slice(ha[0].length) === b.slice(hb[0].length);
}
