/**
 * POST /api/expected-points: the home machine's signed Expected Points push (ADR 0006).
 *
 * In order:
 * - 401 for missing or malformed signature headers, or a timestamp over 5 minutes off. These are
 *   checked before the body is read, so a stranger can't make it read up to 1 MB.
 * - 413 for a body over MAX_BODY_BYTES.
 * - 401 for a bad signature. Every 401 is the same, with no hint of which part failed
 *   (site/contracts/push-signature.json).
 * - 400 with a short reason for invalid JSON, a push failing site/schema/expected-points.schema.json,
 *   a generatedAt in the future, or a generatedAt over MAX_PUSH_AGE_MS (15 minutes) before the
 *   server's now.
 * - 409 for a push for a later NFL Week than KV `scoreboard`'s. ffp rolls to the next week on
 *   Wednesday at 3 AM Eastern, ESPN's scoreboard may still be on the old one, and a push for
 *   another week replaces `expected`, which would wipe the current week's. Accepted while there is
 *   no scoreboard in KV.
 * - 409 for a push older than the stored one, so a delayed retry can't roll Expected Points back;
 *   204 without writing for a repeat of it (same generatedAt, e.g. a retry); 429 for one under
 *   5 minutes after it.
 * Otherwise the push is merged into KV `expected` (kv.ts `ExpectedStore`: frozen at kickoff, a new
 * week replaces) and the answer is 204.
 *
 * Free-plan budget (1,000 KV writes a day, shared with the cron Worker; refresh.ts has the day's
 * sum): per push past the checks, 3 KV reads (`scoreboard`, `expected`, `idmap`), 1 write of
 * `expected`, and 1 of `idmap` only when the id map changed and the stored one is at least an hour
 * old. Accepted pushes' generatedAts are at least 5 minutes apart, and each is within 15 minutes
 * before and 5 minutes after the server's now. So the pushes accepted in one calendar day carry
 * generatedAts within a 1,460-minute span: at most 293 writes of `expected` and 25 of `idmap`,
 * 318 a day (288 + 24 plus the window's edges), however the pushes are stamped. Without the age
 * limit, a sender could stamp pushes stored + 5 minutes, + 10, … up to now after a quiet gap, and
 * every one would write. Without LEADERBOARD_PUSH_SECRET it answers 500 and never accepts.
 */
import { invertIdMap, mergeExpected, sameIdMap } from "../../src/push/merge.js";
import { validatePush, type Push } from "../../src/push/validate.js";
import { checkHeaders, readBody, verifySignature } from "../../src/push/verify.js";
import { KEYS, type ExpectedStore, type IdMapStore, type KV } from "../../src/worker/kv.js";

/** As in site/contracts/push-signature.json (a test holds them equal). */
export const MAX_SKEW_SECONDS = 300;
export const MAX_BODY_BYTES = 1_048_576;
/** The least time between accepted pushes' generatedAt (ffp pushes every 30 minutes). */
export const MIN_PUSH_SPACING_MS = 5 * 60_000;
/**
 * The oldest generatedAt accepted, before the server's now. ffp pushes a slate about a minute
 * after generating it (55 s of pricing), and retries once at once.
 */
export const MAX_PUSH_AGE_MS = 15 * 60_000;
/** The least time between writes of a changed `idmap` (it only joins ESPN's fallback projections). */
export const MIN_IDMAP_SPACING_MS = 60 * 60_000;
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

  const headers = checkHeaders({
    timestamp: request.headers.get("X-FFP-Timestamp"),
    signature: request.headers.get("X-FFP-Signature"),
    now,
    maxSkewSeconds: MAX_SKEW_SECONDS,
  });
  if (headers === null) return answer(401, "unauthorized");
  const body = await readBody(request, MAX_BODY_BYTES);
  if (body === null) return answer(413, `body over ${MAX_BODY_BYTES} bytes`);
  if (!(await verifySignature(secret, headers, body))) return answer(401, "unauthorized");

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
  // The 5-minute spacing below is on generatedAt, the sender's clock; this ties it to the server's.
  if (generatedAt < now.getTime() - MAX_PUSH_AGE_MS) return answer(400, "generatedAt is over 15 minutes old");

  const scoreboardWeek = await readScoreboardWeek(env.LEADERBOARD);
  if (scoreboardWeek !== null && compareWeeks(push, scoreboardWeek) > 0) {
    return answer(409, `for a later NFL Week than the scoreboard's (${scoreboardWeek.season} ${scoreboardWeek.seasonType} week ${scoreboardWeek.week})`);
  }

  const stored = await readStore<ExpectedStore>(env.LEADERBOARD, KEYS.expected);
  if (stored !== null) {
    const storedAt = Date.parse(stored.generatedAt);
    if (generatedAt < storedAt) return answer(409, `older than the stored push (${stored.generatedAt})`);
    if (generatedAt === storedAt) return new Response(null, { status: 204 });
    if (generatedAt - storedAt < MIN_PUSH_SPACING_MS) {
      const wait = Math.ceil((storedAt + MIN_PUSH_SPACING_MS - generatedAt) / 1000);
      return answer(429, "under 5 minutes after the stored push", { "Retry-After": String(wait) });
    }
  }

  // idmap first: `expected` is written last, so a push whose writes failed halfway isn't taken
  // for a repeat (same generatedAt) when it's retried.
  const espnToSleeper = invertIdMap(push.idMap);
  const idmap = await readStore<IdMapStore>(env.LEADERBOARD, KEYS.idmap);
  const idmapDue = idmap === null || generatedAt - Date.parse(idmap.generatedAt) >= MIN_IDMAP_SPACING_MS;
  if (idmapDue && (idmap === null || !sameIdMap(idmap.espnToSleeper, espnToSleeper))) {
    const next: IdMapStore = { schemaVersion: 1, generatedAt: push.generatedAt, espnToSleeper };
    await env.LEADERBOARD.put(KEYS.idmap, JSON.stringify(next));
  }
  await env.LEADERBOARD.put(KEYS.expected, JSON.stringify(mergeExpected(stored, push)));
  return new Response(null, { status: 204 });
}

type WeekKey = Pick<Push, "season" | "seasonType" | "week">;
const SEASON_TYPE_ORDER = { regular: 0, postseason: 1 } as const;

/** Negative, 0 or positive as week a is before, the same as, or after week b. */
export function compareWeeks(a: WeekKey, b: WeekKey): number {
  return a.season - b.season || SEASON_TYPE_ORDER[a.seasonType] - SEASON_TYPE_ORDER[b.seasonType] || a.week - b.week;
}

/** KV `scoreboard`'s NFL Week (refresh.ts writes it), or null if absent or unreadable (logged). */
async function readScoreboardWeek(kv: KV): Promise<WeekKey | null> {
  const text = await kv.get(KEYS.scoreboard, { type: "text" });
  if (text === null) return null;
  try {
    const week = (JSON.parse(text) as { week?: { season?: unknown; seasonType?: unknown; number?: unknown } } | null)?.week;
    if (Number.isInteger(week?.season) && (week?.seasonType === "regular" || week?.seasonType === "postseason") && Number.isInteger(week?.number)) {
      return { season: week.season as number, seasonType: week.seasonType, week: week.number as number };
    }
  } catch {
    // fall through
  }
  console.error("expected-points: KV scoreboard is unreadable; the push's week isn't checked against it");
  return null;
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
