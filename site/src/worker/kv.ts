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

export const KEYS = { scoreboard: "scoreboard", leaderboard: "leaderboard" } as const;

/**
 * The stored Leaderboard always starts like this (leaderboard.ts `serialize`), so updatedAt is
 * found and set aside without parsing the whole text.
 */
export const LEADERBOARD_HEAD = /^\{"schemaVersion":1,"updatedAt":"([^"]*)"/;

/** updatedAt from a stored Leaderboard, or null if the text isn't laid out as `serialize` writes it. */
export function updatedAtOf(text: string): string | null {
  return LEADERBOARD_HEAD.exec(text)?.[1] ?? null;
}
