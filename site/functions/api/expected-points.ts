/**
 * POST /api/expected-points: the home machine's signed Expected Points push (ADR 0006).
 *
 * In order: a body over MAX_BODY_BYTES is 413; a bad or stale signature is 401, with no hint of
 * which part failed (site/contracts/push-signature.json); invalid JSON or a push failing
 * site/schema/expected-points.schema.json is 400 with a short reason; a push older than the
 * stored one is 409, so a delayed retry can't roll Expected Points back. Otherwise it is merged
 * into KV `expected` (kv.ts `ExpectedStore`: frozen at kickoff, a new week replaces) and 204.
 *
 * Free-plan budget: per push, 2 KV reads, 1 write of `expected`, and 1 of `idmap` only when the
 * id map changed (refresh.ts has the day's write budget). A repeat of the stored push (same
 * generatedAt) writes nothing. Without LEADERBOARD_PUSH_SECRET it answers 500 and never accepts.
 */
import { invertIdMap, mergeExpected, sameIdMap } from "../../src/push/merge.js";
import { validatePush } from "../../src/push/validate.js";
import { readBody, verifyPush } from "../../src/push/verify.js";
import { KEYS, type ExpectedStore, type IdMapStore, type KV } from "../../src/worker/kv.js";

/** As in site/contracts/push-signature.json (a test holds them equal). */
export const MAX_SKEW_SECONDS = 300;
export const MAX_BODY_BYTES = 1_048_576;
/** A secret shorter than this is a misconfiguration (setup_cloudflare.sh makes 64 hex characters). */
const MIN_SECRET_LENGTH = 32;

/** This Function's bindings: kv.ts `Env` plus the Pages secret. */
export interface PushEnv {
  LEADERBOARD: KV;
  LEADERBOARD_PUSH_SECRET?: string;
}

export function onRequest({ request, env }: { request: Request; env: PushEnv }): Promise<Response> {
  return handlePush(request, env, new Date());
}

export async function handlePush(request: Request, env: PushEnv, now: Date): Promise<Response> {
  if (request.method !== "POST") return answer(405, "POST only", { Allow: "POST" });
  const secret = env.LEADERBOARD_PUSH_SECRET;
  if (secret === undefined || secret.length < MIN_SECRET_LENGTH) {
    console.error("expected-points: LEADERBOARD_PUSH_SECRET is not set (or too short); refusing every push");
    return answer(500, "not configured");
  }

  const body = await readBody(request, MAX_BODY_BYTES);
  if (body === null) return answer(413, `body over ${MAX_BODY_BYTES} bytes`);
  const authentic = await verifyPush({
    secret,
    timestamp: request.headers.get("X-FFP-Timestamp"),
    signature: request.headers.get("X-FFP-Signature"),
    body,
    now,
    maxSkewSeconds: MAX_SKEW_SECONDS,
  });
  if (!authentic) return answer(401, "unauthorized");

  let value: unknown;
  try {
    value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    return answer(400, "body is not UTF-8 JSON");
  }
  const checked = validatePush(value);
  if (!checked.ok) return answer(400, checked.reason);
  const push = checked.push;
  const generatedAt = Date.parse(push.generatedAt);
  // A future generatedAt would make every later push look older (409) until that time.
  if (generatedAt > now.getTime() + MAX_SKEW_SECONDS * 1000) return answer(400, "generatedAt is in the future");

  const stored = await readStore<ExpectedStore>(env.LEADERBOARD, KEYS.expected);
  if (stored !== null) {
    const storedAt = Date.parse(stored.generatedAt);
    if (generatedAt < storedAt) return answer(409, `older than the stored push (${stored.generatedAt})`);
    if (generatedAt === storedAt) return new Response(null, { status: 204 });
  }

  // idmap first: `expected` is written last, so a push whose writes failed halfway isn't taken
  // for a repeat (same generatedAt) when it's retried.
  const espnToSleeper = invertIdMap(push.idMap);
  const idmap = await readStore<IdMapStore>(env.LEADERBOARD, KEYS.idmap);
  if (idmap === null || !sameIdMap(idmap.espnToSleeper, espnToSleeper)) {
    const next: IdMapStore = { schemaVersion: 1, generatedAt: push.generatedAt, espnToSleeper };
    await env.LEADERBOARD.put(KEYS.idmap, JSON.stringify(next));
  }
  await env.LEADERBOARD.put(KEYS.expected, JSON.stringify(mergeExpected(stored, push)));
  return new Response(null, { status: 204 });
}

/** A stored store, or null if absent or unreadable (logged; the push then replaces it). */
async function readStore<T extends { schemaVersion: 1; generatedAt: string }>(kv: KV, key: string): Promise<T | null> {
  const text = await kv.get(key, { type: "text" });
  if (text === null) return null;
  try {
    const value = JSON.parse(text) as T;
    if (value?.schemaVersion === 1 && typeof value.generatedAt === "string" && !Number.isNaN(Date.parse(value.generatedAt))) return value;
  } catch {
    // fall through
  }
  console.error(`expected-points: KV ${key} is unreadable; this push replaces it`);
  return null;
}

function answer(status: number, error: string, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify({ error }), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers },
  });
}
