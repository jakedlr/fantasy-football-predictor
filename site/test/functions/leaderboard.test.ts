import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { onRequestGet } from "../../functions/api/leaderboard.js";
import { KEYS } from "../../src/worker/refresh.js";
import { FakeKV, readJson } from "../helpers/fakes.js";

const example = readJson("schema/examples/leaderboard.json");
// Laid out as the Worker writes it: schemaVersion, then updatedAt.
const body = JSON.stringify(example);
const toml = readFileSync(new URL("../../wrangler.toml", import.meta.url), "utf8");

function get(kv: FakeKV, headers: Record<string, string> = {}) {
  const request = new Request("https://ffp-leaderboard.pages.dev/api/leaderboard", { headers });
  return onRequestGet({ request, env: { LEADERBOARD: kv } });
}

describe("GET /api/leaderboard", () => {
  it("serves the KV Leaderboard as JSON, cached 60 s, with an ETag", async () => {
    const kv = new FakeKV({ [KEYS.leaderboard]: body });
    const res = await get(kv);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/json; charset=utf-8");
    expect(res.headers.get("cache-control")).toBe("public, max-age=60");
    expect(res.headers.get("etag")).toBe(`"${example.updatedAt}"`);
    expect(await res.text()).toBe(body);
    expect(kv.reads.map((r) => r.key)).toEqual([KEYS.leaderboard]);
  });

  it.each([
    ["the same ETag", `"${example.updatedAt}"`],
    ["a weak form of it", `W/"${example.updatedAt}"`],
    ["one of a list", `"old", "${example.updatedAt}"`],
    ["*", "*"],
  ])("answers 304 with no body to If-None-Match of %s", async (_what, header) => {
    const res = await get(new FakeKV({ [KEYS.leaderboard]: body }), { "If-None-Match": header });
    expect(res.status).toBe(304);
    expect(res.headers.get("etag")).toBe(`"${example.updatedAt}"`);
    expect(res.headers.get("cache-control")).toBe("public, max-age=60");
    expect(await res.text()).toBe("");
  });

  it("answers 200 to a stale ETag", async () => {
    const res = await get(new FakeKV({ [KEYS.leaderboard]: body }), { "If-None-Match": `"2026-11-22T20:00:00Z"` });
    expect(res.status).toBe(200);
  });

  it("answers 503 with a small JSON error, not cached, before the Worker's first write", async () => {
    const res = await get(new FakeKV());
    expect(res.status).toBe(503);
    expect(res.headers.get("content-type")).toBe("application/json; charset=utf-8");
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.json()).toEqual({ error: expect.stringMatching(/not available/) });
  });

  it("falls back to a hash ETag if the stored text has no readable updatedAt", async () => {
    const res = await get(new FakeKV({ [KEYS.leaderboard]: '{"odd":true}' }));
    expect(res.status).toBe(200);
    expect(res.headers.get("etag")).toMatch(/^"[0-9a-z]+"$/);
  });
});

describe("site/wrangler.toml (Pages)", () => {
  it("names the Pages project, serves public/ and binds KV as LEADERBOARD", () => {
    expect(toml).toMatch(/^name\s*=\s*"ffp-leaderboard"$/m);
    expect(toml).toMatch(/^pages_build_output_dir\s*=\s*"public"$/m);
    expect(toml).toMatch(/binding\s*=\s*"LEADERBOARD"/);
    expect(toml).not.toMatch(/account_id/);
  });
});
