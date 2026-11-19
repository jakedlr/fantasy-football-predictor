// Throwaway S9.1 Live test recorder. Every minute (cron) inside RECORD_FROM..RECORD_UNTIL it
// fetches the same three URLs as scripts/record_live.py and stores one KV value per minute:
// three manifest lines (status, latency, size, game clock, stats hash; no bodies).
// GET /dump returns every stored line as JSONL for scripts/analyze_live_spike.py.
import {
  boxscoreSlice,
  countPlays,
  inWindow,
  type ManifestRecord,
  manifestRecord,
  scoreboardInfo,
  sha16,
  type Source,
  SOURCES,
  sourceUrls,
  toJsonl,
  USER_AGENT,
} from "./record";

export interface Env {
  SPIKE: KVNamespace;
  EVENT_ID: string;
  SEASON: string;
  WEEK: string;
  RECORD_FROM: string;
  RECORD_UNTIL: string;
}

const BLOCKED = new Set([403, 429]);
const MAX_BACKOFF_MIN = 16;

async function request(source: Source, url: string, env: Env, now: Date): Promise<ManifestRecord> {
  // Backoff state lives in KV only while a source is blocked, so normal minutes cost no extra writes.
  const backoff = await env.SPIKE.get<{ until: number; penalty: number }>(`backoff:${source}`, "json");
  if (backoff && now.getTime() < backoff.until) {
    return manifestRecord({ ts: now, source, url, skipped: "backoff" });
  }
  const started = Date.now();
  let resp: Response;
  try {
    resp = await fetch(url, { headers: { "User-Agent": USER_AGENT, Accept: "application/json" } });
  } catch (e) {
    return manifestRecord({ ts: now, source, url, latency_ms: Date.now() - started, error: String(e).slice(0, 300) });
  }
  const text = await resp.text();
  const rec = manifestRecord({
    ts: now, source, url, status: resp.status, latency_ms: Date.now() - started, bytes: text.length,
    cache_control: resp.headers.get("cache-control"), age: resp.headers.get("age"),
    cf_cache_status: resp.headers.get("cf-cache-status"), retry_after: resp.headers.get("retry-after"),
  });
  if (BLOCKED.has(resp.status)) {
    const penalty = Math.min(Math.max(1, (backoff?.penalty ?? 0) * 2), MAX_BACKOFF_MIN);
    await env.SPIKE.put(`backoff:${source}`, JSON.stringify({ until: now.getTime() + penalty * 60_000, penalty }),
      { expirationTtl: 86_400 });
    console.log(`${source}: HTTP ${resp.status}, backing off ${penalty} min`);
    return rec;
  }
  if (backoff) await env.SPIKE.delete(`backoff:${source}`);
  if (resp.status !== 200) return { ...rec, error: `HTTP ${resp.status}` };
  if (source === "espn-scoreboard") {
    const { statsText, error, ...info } = scoreboardInfo(text, env.EVENT_ID);
    return { ...rec, ...info, error: error ?? null, stats_hash: statsText ? await sha16(statsText) : null };
  }
  if (source === "espn-summary") {
    const box = boxscoreSlice(text);
    return {
      ...rec, plays: countPlays(text), stats_hash: await sha16(box ?? text),
      error: box === null ? "boxscore not found; hashed the whole body" : null,
    };
  }
  return { ...rec, stats_hash: await sha16(text) };
}

export async function recordOnce(env: Env, now: Date): Promise<ManifestRecord[] | null> {
  if (!inWindow(now, env)) return null;
  const urls = sourceUrls(env.EVENT_ID, Number(env.SEASON), Number(env.WEEK));
  const records = [];
  for (const source of SOURCES) records.push(await request(source, urls[source], env, now));
  await env.SPIKE.put(`rec:${now.toISOString()}`, toJsonl(records), { expirationTtl: 14 * 86_400 });
  return records;
}

async function dump(env: Env): Promise<string> {
  const keys: string[] = [];
  let cursor: string | undefined;
  do {
    const page = await env.SPIKE.list({ prefix: "rec:", cursor });
    keys.push(...page.keys.map((k) => k.name));
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  keys.sort();
  const values = await Promise.all(keys.map((k) => env.SPIKE.get(k)));
  return values.filter((v): v is string => v !== null).join("");
}

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(recordOnce(env, new Date(controller.scheduledTime)));
  },

  async fetch(req: Request, env: Env): Promise<Response> {
    const path = new URL(req.url).pathname;
    if (path === "/dump") {
      return new Response(await dump(env), { headers: { "content-type": "application/x-ndjson" } });
    }
    return new Response("ffp live spike recorder: GET /dump\n", { status: 404 });
  },
};
