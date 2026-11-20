/**
 * GET /api/leaderboard: the Leaderboard the cron Worker keeps in KV (ADR 0006).
 *
 * The ETag is the Leaderboard's updatedAt, read from the start of the text without parsing it
 * (the Worker rewrites updatedAt whenever anything changes). Free-plan budget: every call counts
 * toward 100,000 Function requests and 100,000 KV reads a day.
 */
import { KEYS, updatedAtOf, type Env } from "../../src/worker/kv.js";

const JSON_TYPE = "application/json; charset=utf-8";
const CACHE = "public, max-age=60";

export async function onRequestGet({ request, env }: { request: Request; env: Env }): Promise<Response> {
  // cacheTtl 60: KV may answer from its edge cache for up to 60 s, matching max-age.
  const text = await env.LEADERBOARD.get(KEYS.leaderboard, { type: "text", cacheTtl: 60 });
  if (text === null) {
    return new Response(JSON.stringify({ error: "Leaderboard not available yet; try again in a few minutes." }), {
      status: 503,
      headers: { "Content-Type": JSON_TYPE, "Cache-Control": "no-store", "Retry-After": "300" },
    });
  }
  const etag = `"${updatedAtOf(text) ?? fnv1a(text)}"`;
  const headers = { ETag: etag, "Cache-Control": CACHE };
  if (matches(request.headers.get("If-None-Match"), etag)) return new Response(null, { status: 304, headers });
  return new Response(text, { status: 200, headers: { ...headers, "Content-Type": JSON_TYPE } });
}

/** If-None-Match uses weak comparison: `W/"x"` matches `"x"`; `*` matches anything. */
function matches(header: string | null, etag: string): boolean {
  if (header === null) return false;
  return header.split(",").some((tag) => {
    const t = tag.trim();
    return t === "*" || t.replace(/^W\//, "") === etag;
  });
}

/** A fallback ETag for text without a readable updatedAt (shouldn't happen). */
function fnv1a(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36);
}
