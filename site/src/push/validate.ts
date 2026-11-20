/**
 * The Expected Points push checked against site/schema/expected-points.schema.json, plus the rules
 * a schema can't state (one entry per Leaderboard id; a D/ST's Sleeper id is its team).
 *
 * Workers forbid code generation (`new Function`), so Ajv's compiled validators can't run there.
 * @cfworker/json-schema interprets the schema instead; test/push/validate.test.ts holds it to the
 * test suite's Ajv on the example and on invalid pushes.
 */
import { Validator } from "@cfworker/json-schema";
import schema from "../../schema/expected-points.schema.json";
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

// shortCircuit: stop at the first error; a rejected push only needs one reason.
const validator = new Validator(schema as object, "2020-12", true);

/** The Leaderboard id a pushed player is stored under (kv.ts ExpectedStore). */
export function leaderboardId(p: Pick<PushPlayer, "position" | "team" | "sleeperId">): string {
  return p.position === "D/ST" ? `dst:${p.team}` : `sleeper:${p.sleeperId}`;
}

export function validatePush(value: unknown): Checked {
  const result = validator.validate(value);
  if (!result.valid) {
    // The last unit is the innermost failure, e.g. `/players/1/statMeans: Property name "fgm_50p" …`.
    const unit = result.errors[result.errors.length - 1];
    return { ok: false, reason: short(unit ? `${unit.instanceLocation || "/"}: ${unit.error}` : "does not match the schema") };
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
