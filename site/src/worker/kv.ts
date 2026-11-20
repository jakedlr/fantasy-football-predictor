/**
 * What the cron Worker and the Pages Function share about KV: the binding, the keys, and how the
 * stored Leaderboard text begins. Kept free of the scoring code, so the Pages Function's bundle
 * stays small.
 *
 * `KV` is the slice of Cloudflare's KVNamespace they use, declared here rather than taken from
 * @cloudflare/workers-types, so the handlers type-check and test in plain Node with a Map-backed
 * fake (test/helpers/fakes.ts). A real KVNamespace satisfies it.
 */
export interface KV {
  get(key: string, options: { type: "text"; cacheTtl?: number }): Promise<string | null>;
  put(key: string, value: string): Promise<void>;
}

/** The bindings in worker/wrangler.toml and wrangler.toml (Pages). */
export interface Env {
  LEADERBOARD: KV;
}

/**
 * - `scoreboard`: the scoreboard run's parsed ESPN scoreboard (refresh.ts).
 * - `leaderboard`: what /api/leaderboard serves (leaderboard.ts `serialize`).
 * - `expected`: ffp's Expected Points, an `ExpectedStore`, written by /api/expected-points.
 * - `idmap`: Sleeper id → ESPN id, an `IdMapStore`, written by /api/expected-points only when it changes.
 * - `espn`: ESPN's projections rescored, an `EspnStore`, written by the ESPN projections run.
 */
export const KEYS = {
  scoreboard: "scoreboard",
  leaderboard: "leaderboard",
  expected: "expected",
  idmap: "idmap",
  espn: "espn",
} as const;

export type Position = "QB" | "RB" | "WR" | "TE" | "K" | "D/ST";

/**
 * A player's pregame expectation for one Game (Sprint 11). `statMeans` keys are as in
 * schema/expected-points.schema.json (offense: ffp.scoring's stats; K: pat_made, pat_missed,
 * fg_missed and Sleeper's fgm_* buckets; D/ST: ffp.scoring's D/ST stats plus points_allowed_all,
 * opponent_*_tds_points and yards_allowed); a missing stat is 0. `expected` has one entry per
 * Leaderboard Scoring Rules option.
 */
export interface Expectation {
  name: string;
  team: string;
  position: Position;
  /** The Game's kickoff, RFC 3339 UTC. */
  kickoff: string;
  statMeans: Record<string, number>;
  expected: Record<string, number>;
}

/**
 * KV `expected`. Players are keyed by Leaderboard id (`sleeper:<id>`, `dst:<TEAM>`). Merging a push
 * (/api/expected-points): a push for another season/seasonType/week replaces the store; for the same
 * week, a player whose stored kickoff is at or before the push's generatedAt keeps his stored entry
 * (frozen at kickoff), and every other player takes the push's entry. A stored player missing from a
 * push whose Game hasn't kicked off is dropped (ruled out, or no longer priced).
 */
export interface ExpectedStore {
  schemaVersion: 1;
  season: number;
  seasonType: "regular" | "postseason";
  week: number;
  /** generatedAt of the latest push merged in: the Leaderboard's `expectedPointsAsOf`. */
  generatedAt: string;
  players: Record<string, Expectation>;
}

/** KV `idmap`: the latest push's `idMap`, ESPN id → Sleeper id (inverted from the push). */
export interface IdMapStore {
  schemaVersion: 1;
  generatedAt: string;
  espnToSleeper: Record<string, string>;
}

/**
 * KV `espn`: ESPN's weekly projections for the scoreboard's NFL Week, converted to the same stat
 * keys and rescored under every Scoring Rules option. Keyed by Leaderboard id: players through
 * `idmap` (unmapped ESPN players are left out), D/STs by team. Used for a player with no `expected`
 * entry, as expectedSource "espn". Frozen at kickoff like `expected`: the run keeps a stored entry
 * whose kickoff has passed.
 */
export interface EspnStore {
  schemaVersion: 1;
  season: number;
  seasonType: "regular" | "postseason";
  week: number;
  fetchedAt: string;
  players: Record<string, Expectation>;
}

/**
 * The stored Leaderboard always starts like this (leaderboard.ts `serialize`), so updatedAt is
 * found and set aside without parsing the whole text.
 */
export const LEADERBOARD_HEAD = /^\{"schemaVersion":1,"updatedAt":"([^"]*)"/;

/** updatedAt from a stored Leaderboard, or null if the text isn't laid out as `serialize` writes it. */
export function updatedAtOf(text: string): string | null {
  return LEADERBOARD_HEAD.exec(text)?.[1] ?? null;
}
