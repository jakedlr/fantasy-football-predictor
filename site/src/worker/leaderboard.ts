/**
 * Scoreboard + Sleeper players + Expected Points → the Leaderboard JSON
 * (site/schema/leaderboard.schema.json). Pure: no fetch, no KV, no clock.
 *
 * Week Points per Game Status (GLOSSARY.md, PLAN.md "Ranking" and "Projected Total"):
 * - Upcoming: Expected Points (0, with `expected` null, for a player who has none); Points So Far null.
 * - Live: the Projected Total, each Scoring Stat's current value + its pregame mean × the fraction of
 *   the Game left (`fractionLeft`), then scored, so D/ST tiers and kick distances stay right. A
 *   player with no expectation has Week Points = Points So Far.
 * - Final: Points So Far.
 * `expected` is the frozen Expected Points in every Game Status. Expected Points come from KV
 * `expected` (ffp, expectedSource "ffp"), else KV `espn` (ESPN's projection rescored, "espn").
 *
 * It runs on every stats run inside the free Worker's 10 ms of CPU, for about 2,000 Sleeper rows
 * and 1,000 expectations: one pass over each, one projected stat line per Live player with an
 * expectation, scored under every Scoring Rules option (test/worker/cpu.bench.test.ts).
 */
import { LEADERBOARD_HEAD, updatedAtOf, type EspnStore, type Expectation, type ExpectedStore, type Position } from "./kv.js";
import type { Game, Scoreboard, Week } from "./scoreboard.js";
import { RULES, RULES_NAMES, scoreLine, type AnyStatLine } from "./scoring.js";
import type { SleeperPlayer } from "./sleeper.js";

export interface PointsEntry {
  /** Week Points: the ranking number. */
  week: number;
  /** Points So Far; null when Upcoming. */
  soFar: number | null;
  /** Expected Points, frozen at kickoff; null when the player has none. */
  expected: number | null;
}

export interface LeaderboardPlayer {
  id: string;
  name: string;
  team: string;
  position: Position;
  /** ESPN event id; the page finds the opponent through it. */
  gameId: string;
  /**
   * Ruled out after the list was built. Always false for now: neither Sleeper's stats rows nor
   * ESPN's scoreboard carry an injury or inactive flag the Worker could read.
   */
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

/** KV `expected` and `espn` as the stats run read them; null when missing or unreadable. */
export interface ExpectationStores {
  expected?: ExpectedStore | null;
  espn?: EspnStore | null;
}

/** Points to 2 decimals, which also clears float noise (0.1 × 3) and "-0". */
export function round2(x: number): number {
  return Math.round(x * 100) / 100 || 0;
}

/** A player is listed with no stat recorded only if his Expected Points (default rules) reach this. */
export const MIN_EXPECTED_POINTS = 1;

const QUARTER = 900;
const REGULATION = 4 * QUARTER;
const CLOCK = /^(\d{1,2}):(\d{2})$/;

/**
 * The fraction of a Live Game left, from its quarter and clock (research/leaderboard-projections.md
 * C2): (clock seconds + 900 × quarters still to start) / 3600, clamped to 0..1.
 * - Halftime and the end of a quarter: ESPN shows the quarter just ended at 0:00, so halftime is
 *   Q2 0:00 → 0.5, the end of Q3 is 0.25 and the end of regulation 0.
 * - Overtime (period 5+) counts as nothing left: 0.
 * - No period (not seen once the scoreboard parses): the Game has just gone Live, 1.
 * - A period but no readable clock: that quarter counts as played out.
 */
export function fractionLeft(game: Pick<Game, "period" | "clock">): number {
  if (game.period === null) return 1;
  const period = Math.max(1, game.period);
  if (period > 4) return 0;
  const m = game.clock === null ? null : CLOCK.exec(game.clock);
  const seconds = m ? Math.min(QUARTER, Number(m[1]) * 60 + Number(m[2])) : 0;
  return Math.min(1, Math.max(0, (seconds + QUARTER * (4 - period)) / REGULATION));
}

const DEFAULT_RULE = RULES_NAMES[0]!;
const RULE_PAIRS = RULES_NAMES.map((name) => [name, RULES[name]!] as const);
const NONE: Readonly<Record<string, Expectation>> = Object.freeze({});
const EMPTY_STATS = Object.freeze({});
const LEADERBOARD_ID = /^(sleeper:\d+|dst:[A-Z]{2,3})$/;

/** A store counts only for the scoreboard's NFL Week. */
function forWeek<S extends ExpectedStore | EspnStore>(store: S | null | undefined, week: Week): S | null {
  if (!store || store.season !== week.season || store.seasonType !== week.seasonType || store.week !== week.number) return null;
  return store;
}

function expectedUnder(e: Expectation, rule: string): number | null {
  const x = e.expected[rule];
  return typeof x === "number" && Number.isFinite(x) ? round2(x) : null;
}

function hasStat(stats: object): boolean {
  for (const _ in stats) return true;
  return false;
}

function kindOf(position: Position): AnyStatLine["kind"] {
  return position === "K" ? "kicker" : position === "D/ST" ? "dst" : "offense";
}

/** current + mean × f for every stat the expectation has a mean for; other current stats as they are. */
function projectLine(line: AnyStatLine, means: Readonly<Record<string, number>>, f: number): AnyStatLine {
  const cur = line.stats as Record<string, unknown>;
  const out: Record<string, unknown> = { ...cur };
  for (const key in means) {
    const mean = means[key];
    if (typeof mean !== "number" || !mean) continue;
    const now = cur[key];
    out[key] = (typeof now === "number" ? now : 0) + mean * f;
  }
  return { kind: line.kind, stats: out } as AnyStatLine;
}

function pointsFor(game: Game, f: number, line: AnyStatLine, e: Expectation | undefined): Record<string, PointsEntry> {
  const points: Record<string, PointsEntry> = {};
  const projected = game.status === "live" && e !== undefined && f > 0 ? projectLine(line, e.statMeans, f) : null;
  for (const [name, rules] of RULE_PAIRS) {
    const expected = e === undefined ? null : expectedUnder(e, name);
    if (game.status === "upcoming") {
      points[name] = { week: expected ?? 0, soFar: null, expected };
    } else {
      const soFar = round2(scoreLine(line, rules));
      const week = projected === null ? soFar : round2(scoreLine(projected, rules));
      points[name] = { week, soFar, expected };
    }
  }
  return points;
}

/**
 * Every player whose team plays this NFL Week (a bye or a canceled Game leaves him out), from the
 * union of Sleeper's rows and the expectations, joined to his Game by team (Sleeper's when he has a
 * row). Listed once he has recorded a stat (a non-empty Sleeper stat line, zeros included) or has
 * Expected Points ≥ MIN_EXPECTED_POINTS under the default Scoring Rules; a Sleeper row with an
 * empty line (Sleeper lists active players before they touch the ball) is not enough on its own.
 * Players are ordered by id, so the same inputs always serialize to the same text.
 *
 * A store for another season, season type or week than the scoreboard's is ignored.
 * `expectedPointsAsOf` is the `expected` store's generatedAt.
 */
export function buildLeaderboard(
  scoreboard: Scoreboard,
  sleeperPlayers: readonly SleeperPlayer[],
  updatedAt: string,
  stores: ExpectationStores = {},
): Leaderboard {
  const gameByTeam = new Map<string, Game>();
  const fByGame = new Map<string, number>();
  for (const g of scoreboard.games) {
    gameByTeam.set(g.home, g);
    gameByTeam.set(g.away, g);
    fByGame.set(g.id, g.status === "live" ? fractionLeft(g) : 0);
  }
  const ffp = forWeek(stores.expected, scoreboard.week);
  const espn = forWeek(stores.espn, scoreboard.week);
  const ffpPlayers = ffp?.players ?? NONE;
  const espnPlayers = espn?.players ?? NONE;
  const own = (store: Readonly<Record<string, Expectation>>, id: string) => (Object.hasOwn(store, id) ? store[id] : undefined);

  const players: LeaderboardPlayer[] = [];
  const seen = new Set<string>();
  for (const p of sleeperPlayers) {
    if (seen.has(p.id)) continue;
    seen.add(p.id);
    const game = gameByTeam.get(p.team);
    if (game === undefined) continue;
    const fromFfp = own(ffpPlayers, p.id);
    const e = fromFfp ?? own(espnPlayers, p.id);
    if (!hasStat(p.stats) && !(e !== undefined && (expectedUnder(e, DEFAULT_RULE) ?? 0) >= MIN_EXPECTED_POINTS)) continue;
    players.push({
      id: p.id, name: p.name, team: p.team, position: p.position, gameId: game.id, out: false,
      expectedSource: fromFfp ? "ffp" : e ? "espn" : null,
      points: pointsFor(game, fByGame.get(game.id)!, p, e),
    });
  }
  // Players with an expectation but no Sleeper row: ffp's first, so its entry wins over ESPN's.
  for (const [expectedSource, store] of [["ffp", ffpPlayers], ["espn", espnPlayers]] as const) {
    for (const id in store) {
      if (seen.has(id)) continue;
      seen.add(id);
      const e = store[id]!;
      const game = gameByTeam.get(e.team);
      if (game === undefined || !LEADERBOARD_ID.test(id)) continue;
      if ((expectedUnder(e, DEFAULT_RULE) ?? 0) < MIN_EXPECTED_POINTS) continue;
      const line = { kind: kindOf(e.position), stats: EMPTY_STATS } as AnyStatLine;
      players.push({
        id, name: e.name, team: e.team, position: e.position, gameId: game.id, out: false, expectedSource,
        points: pointsFor(game, fByGame.get(game.id)!, line, e),
      });
    }
  }
  players.sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));

  return {
    schemaVersion: 1,
    updatedAt,
    expectedPointsAsOf: ffp?.generatedAt ?? null,
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
