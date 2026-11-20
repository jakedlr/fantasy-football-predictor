import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ESPN_PROJECTIONS_HOST } from "../../src/worker/espn.js";
import worker, { ESPN_CRON, runCron, SCOREBOARD_CRON, STATS_CRON } from "../../src/worker/index.js";
import { KEYS, SCOREBOARD_URL, type RunDeps } from "../../src/worker/refresh.js";
import { parseScoreboard } from "../../src/worker/scoreboard.js";
import { fakeClock, fakeFetch, FakeKV, readJson } from "../helpers/fakes.js";

const espnLive = readJson("test/fixtures/espn/scoreboard-live.json");
const sleeperLive = readJson("test/fixtures/sleeper/stats-live.json");
const espnWr = readJson("test/fixtures/espn/projections-wr.json");
const toml = readFileSync(new URL("../../worker/wrangler.toml", import.meta.url), "utf8");
const ids = (() => {
  try {
    return readFileSync(new URL("../../.cloudflare-ids.env", import.meta.url), "utf8");
  } catch {
    return null; // gitignored; absent in CI
  }
})();

function deps(kv = new FakeKV()): RunDeps & { kv: FakeKV; calls: string[] } {
  const net = fakeFetch({ [SCOREBOARD_URL]: espnLive, "https://api.sleeper.com/": sleeperLive, [ESPN_PROJECTIONS_HOST]: espnWr });
  return {
    fetch: net.fetch,
    kv,
    now: fakeClock("2026-11-20T02:45:00Z").now,
    log: () => {},
    get calls() {
      return net.calls.map((c) => c.url);
    },
  };
}

describe("runCron", () => {
  it(`runs the scoreboard on ${SCOREBOARD_CRON}`, async () => {
    const d = deps();
    expect(await runCron(SCOREBOARD_CRON, d)).toEqual({ result: "written" });
    expect(d.calls).toEqual([SCOREBOARD_URL]);
    expect(d.kv.writes.map((w) => w.key)).toEqual([KEYS.scoreboard]);
  });

  it(`runs the stats on ${STATS_CRON}`, async () => {
    const d = deps(new FakeKV({ [KEYS.scoreboard]: JSON.stringify(parseScoreboard(espnLive)) }));
    expect(await runCron(STATS_CRON, d)).toEqual({ result: "written" });
    expect(d.calls).toHaveLength(1);
    expect(d.calls[0]).toMatch(/^https:\/\/api\.sleeper\.com\//);
    expect(d.kv.writes.map((w) => w.key)).toEqual([KEYS.leaderboard]);
  });

  it(`runs the ESPN projections on ${ESPN_CRON}`, async () => {
    const d = deps(new FakeKV({ [KEYS.scoreboard]: JSON.stringify(parseScoreboard(espnLive)) }));
    expect(await runCron(ESPN_CRON, d)).toEqual({ result: "written" });
    expect(d.calls).toHaveLength(1);
    expect(d.calls[0]!.startsWith(ESPN_PROJECTIONS_HOST)).toBe(true);
    expect(d.kv.writes.map((w) => w.key)).toEqual([KEYS.espn]);
  });

  it("runs the ESPN projections at minutes the other two runs don't use", () => {
    const minutes = (cron: string) => {
      const m = cron.split(" ")[0]!;
      const step = /^(\*|\d+-\d+)\/(\d+)$/.exec(m);
      if (step) {
        const from = step[1] === "*" ? 0 : Number(step[1]!.split("-")[0]);
        return Array.from({ length: 60 }, (_, i) => i).filter((i) => i >= from && (i - from) % Number(step[2]) === 0);
      }
      return m.split(",").map(Number);
    };
    expect(minutes(ESPN_CRON)).toEqual([9, 29, 49]);
    const taken = new Set([...minutes(SCOREBOARD_CRON), ...minutes(STATS_CRON)]);
    expect(minutes(ESPN_CRON).filter((m) => taken.has(m))).toEqual([]);
  });

  it("refuses a cron it doesn't know", async () => {
    await expect(runCron("0 * * * *", deps())).rejects.toThrow(/unknown cron/);
  });
});

describe("scheduled", () => {
  const controller = (cron: string) => ({ cron, scheduledTime: Date.parse("2026-11-20T02:45:00Z"), noRetry() {} });

  it("throws on a failed run, so Cloudflare's cron log shows it, after keeping the last good data", async () => {
    const kv = new FakeKV();
    // No scoreboard yet: the stats run fails before any fetch.
    await expect(worker.scheduled(controller(STATS_CRON), { LEADERBOARD: kv }, { waitUntil() {}, passThroughOnException() {} })).rejects.toThrow(/scoreboard/);
    expect(kv.writes).toHaveLength(0);
  });
});

describe("worker/wrangler.toml", () => {
  it("schedules exactly the three runs runCron knows", () => {
    const crons = /^crons\s*=\s*\[(.*)\]/m.exec(toml)?.[1];
    expect(crons && [...crons.matchAll(/"([^"]+)"/g)].map((m) => m[1])).toEqual([SCOREBOARD_CRON, STATS_CRON, ESPN_CRON]);
  });

  it("points at this entry and binds KV as LEADERBOARD", () => {
    expect(toml).toMatch(/^main\s*=\s*"\.\.\/src\/worker\/index\.ts"$/m);
    expect(toml).toMatch(/binding\s*=\s*"LEADERBOARD"/);
    expect(toml).toMatch(/^id\s*=\s*"[0-9a-f]{32}"$/m);
    expect(toml).toMatch(/^preview_id\s*=\s*"[0-9a-f]{32}"$/m);
  });

  it("leaves the account id to the environment", () => {
    expect(toml).not.toMatch(/account_id/);
  });

  it.skipIf(ids === null)("uses the KV ids the setup wizard recorded", () => {
    const id = /KV_LEADERBOARD_ID='([0-9a-f]+)'/.exec(ids!)?.[1];
    const preview = /KV_LEADERBOARD_PREVIEW_ID='([0-9a-f]+)'/.exec(ids!)?.[1];
    expect(toml).toContain(`\nid = "${id}"`);
    expect(toml).toContain(`\npreview_id = "${preview}"`);
    const pages = readFileSync(new URL("../../wrangler.toml", import.meta.url), "utf8");
    expect(pages).toMatch(new RegExp(`\\[\\[kv_namespaces\\]\\]\\nbinding = "LEADERBOARD"\\nid = "${id}"`));
    expect(pages).toMatch(new RegExp(`\\[\\[env\\.preview\\.kv_namespaces\\]\\]\\nbinding = "LEADERBOARD"\\nid = "${preview}"`));
  });
});
