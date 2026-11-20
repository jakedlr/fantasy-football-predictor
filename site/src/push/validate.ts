/**
 * The Expected Points push checked against site/schema/expected-points.schema.json, plus the rules
 * a schema can't state (one entry per Leaderboard id; a D/ST's Sleeper id is its team).
 *
 * Workers forbid code generation (`new Function`), so Ajv can't compile the schema there. The
 * schema is compiled ahead of time instead (scripts/build_validator.mjs → expected-points.validate.js,
 * Ajv's standalone code), which also keeps a 1,000-player push well inside the 10 ms CPU limit; an
 * interpreting validator (@cfworker/json-schema 4.1.6) took about 40 ms on Node. test/push/validate.test.ts
 * holds it to the test suite's Ajv and fails while the generated file is stale.
 */
import schemaValidate from "./expected-points.validate.js";
import type { Position } from "../worker/kv.js";

/** One player as pushed (schema `players.items`). */
export interface PushPlayer {
  sleeperId: string;
  gsisId: string | null;
  espnId: string | null;
  name: string;
  team: string;
  position: Position;
  kickoff: string;
  statMeans: Record<string, number>;
  expected: Record<string, number>;
}

/** The push body (expected-points.schema.json). */
export interface Push {
  schemaVersion: 1;
  generatedAt: string;
  season: number;
  seasonType: "regular" | "postseason";
  week: number;
  players: PushPlayer[];
  /** Sleeper id → ESPN id. */
  idMap: Record<string, string>;
}

export type Checked = { ok: true; push: Push } | { ok: false; reason: string };

/** The Leaderboard id a pushed player is stored under (kv.ts ExpectedStore). */
export function leaderboardId(p: Pick<PushPlayer, "position" | "team" | "sleeperId">): string {
  return p.position === "D/ST" ? `dst:${p.team}` : `sleeper:${p.sleeperId}`;
}

export function validatePush(value: unknown): Checked {
  if (!schemaValidate(value)) {
    // Only the first error is kept, e.g. `/players/1/statMeans: must match propertyNames`.
    const error = schemaValidate.errors?.[0];
    return { ok: false, reason: short(error ? `${error.instancePath || "/"}: ${error.message ?? error.keyword}` : "does not match the schema") };
  }
  const push = value as Push;
  const seen = new Set<string>();
  for (const p of push.players) {
    if (p.position === "D/ST" ? p.sleeperId !== p.team : !/^\d+$/.test(p.sleeperId)) {
      return { ok: false, reason: short(`${p.name}: a D/ST's Sleeper id is its team; any other player's is numeric`) };
    }
    const id = leaderboardId(p);
    if (seen.has(id)) return { ok: false, reason: `duplicate player ${id}` };
    seen.add(id);
  }
  return { ok: true, push };
}

function short(text: string): string {
  return text.length <= 200 ? text : `${text.slice(0, 199)}…`;
}
