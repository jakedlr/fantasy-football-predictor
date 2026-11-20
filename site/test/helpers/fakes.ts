import { readFileSync } from "node:fs";
import type { KV } from "../../src/worker/kv.js";

/** A Map-backed KV that records every read and write, for checking the write budget. */
export class FakeKV implements KV {
  readonly data = new Map<string, string>();
  readonly writes: { key: string; value: string }[] = [];
  readonly reads: { key: string; options: unknown }[] = [];
  constructor(initial: Record<string, string> = {}) {
    for (const [k, v] of Object.entries(initial)) this.data.set(k, v);
  }
  async get(key: string, options?: { type: "text"; cacheTtl?: number }): Promise<string | null> {
    this.reads.push({ key, options });
    return this.data.get(key) ?? null;
  }
  async put(key: string, value: string): Promise<void> {
    this.writes.push({ key, value });
    this.data.set(key, value);
  }
}

export interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

export type Route = unknown | Error | { status: number; body?: unknown };

/** A fetch that answers by URL prefix; every call is recorded. An Error value throws (a network failure). */
export function fakeFetch(routes: Record<string, Route>) {
  const calls: FetchCall[] = [];
  const fn = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    calls.push({ url, init });
    const hit = Object.entries(routes).find(([prefix]) => url.startsWith(prefix));
    if (!hit) throw new Error(`fakeFetch: no route for ${url}`);
    const answer = hit[1];
    if (answer instanceof Error) throw answer;
    if (isStatus(answer)) return new Response(JSON.stringify(answer.body ?? null), { status: answer.status });
    return new Response(JSON.stringify(answer), { status: 200, headers: { "content-type": "application/json" } });
  };
  return { fetch: fn as typeof fetch, calls, routes };
}

function isStatus(x: unknown): x is { status: number; body?: unknown } {
  return typeof x === "object" && x !== null && !Array.isArray(x) && "status" in x && Object.keys(x).every((k) => k === "status" || k === "body");
}

/** A clock the test moves by hand. */
export function fakeClock(start: string) {
  let t = Date.parse(start);
  return {
    now: () => new Date(t),
    advance(minutes: number) {
      t += minutes * 60_000;
    },
  };
}

/** A JSON file under site/, e.g. `test/fixtures/espn/scoreboard-live.json`. */
export const readJson = (path: string) => JSON.parse(readFileSync(new URL(`../../${path}`, import.meta.url), "utf8"));
