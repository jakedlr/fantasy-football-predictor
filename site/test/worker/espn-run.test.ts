import { describe, expect, it } from "vitest";
import { ESPN_PROJECTIONS_HOST, PROJECTION_GROUPS, projectionsRequest } from "../../src/worker/espn.js";
import type { EspnStore, IdMapStore } from "../../src/worker/kv.js";
import { KEYS, runEspn, USER_AGENT, type RunDeps } from "../../src/worker/refresh.js";
import { parseScoreboard } from "../../src/worker/scoreboard.js";
import { fakeClock, fakeFetch, FakeKV, readJson, type Route } from "../helpers/fakes.js";

const qbTe = readJson("test/fixtures/espn/projections-qb-te.json");
const rbKDst = readJson("test/fixtures/espn/projections-rb-k-dst.json");
const wr = readJson("test/fixtures/espn/projections-wr.json");
const upcoming = parseScoreboard(readJson("test/fixtures/espn/scoreboard-upcoming.json"));
const postseason = parseScoreboard(readJson("test/fixtures/espn/scoreboard-postseason.json"));

const idmap: IdMapStore = {
  schemaVersion: 1,
  generatedAt: "2026-11-19T20:00:00Z",
  // Josh Allen, Ja'Marr Chase, Jahmyr Gibbs
  espnToSleeper: { "3918298": "4984", "4362628": "7564", "4429795": "9221" },
};

// Minute 9, 29 or 49 of an hour on Thursday, before IND @ HOU kicks off at 01:15Z.
const at = (minute: number) => `2026-11-19T20:${String(minute).padStart(2, "0")}:00Z`;

function setup(routes: Record<string, Route>, initial: Record<string, string> = {}, start = at(9)) {
  const kv = new FakeKV({ [KEYS.scoreboard]: JSON.stringify(upcoming), [KEYS.idmap]: JSON.stringify(idmap), ...initial });
  const net = fakeFetch(routes);
  const clock = fakeClock(start);
  const logs: string[] = [];
  const deps: RunDeps = { fetch: net.fetch, kv, now: clock.now, log: (m) => logs.push(m) };
  return { deps, kv, net, clock, logs };
}
const espnStore = (kv: FakeKV): EspnStore => JSON.parse(kv.data.get(KEYS.espn)!);

describe("ESPN projections run", () => {
  it("fetches this run's group with the filter header and our User-Agent, and writes KV espn", async () => {
    const { deps, kv, net, logs } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe });
    expect(await runEspn(deps)).toEqual({ result: "written" });
    const { url, filter } = projectionsRequest(upcoming.week, PROJECTION_GROUPS[0]!);
    expect(net.calls.map((c) => c.url)).toEqual([url]);
    const headers = new Headers(net.calls[0]!.init?.headers);
    expect(headers.get("x-fantasy-filter")).toBe(filter);
    expect(headers.get("user-agent")).toBe(USER_AGENT);
    expect(net.calls[0]!.init?.signal).toBeInstanceOf(AbortSignal);

    const store = espnStore(kv);
    expect(store).toMatchObject({ schemaVersion: 1, season: 2026, seasonType: "regular", week: 11, fetchedAt: at(9) });
    expect(Object.keys(store.players)).toEqual(["sleeper:4984"]);
    expect(store.players["sleeper:4984"]).toMatchObject({ name: "Josh Allen", team: "BUF", position: "QB", kickoff: "2026-11-22T18:00:00Z" });
    expect(logs.join("\n")).toMatch(/\(QB, TE\): 1 players, \d+ unmapped, 0 not playing/);
  });

  it("fills the store over the hour's three runs, one group each", async () => {
    const { deps, kv, net, clock } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe });
    await runEspn(deps);
    clock.advance(20);
    net.routes[ESPN_PROJECTIONS_HOST] = rbKDst;
    await runEspn(deps);
    clock.advance(20);
    net.routes[ESPN_PROJECTIONS_HOST] = wr;
    await runEspn(deps);
    const keys = Object.keys(espnStore(kv).players);
    expect(keys).toEqual(expect.arrayContaining(["sleeper:4984", "sleeper:9221", "sleeper:7564", "dst:PIT", "dst:HOU"]));
    expect(keys.filter((k) => k.startsWith("dst:"))).toHaveLength(6);
    expect(kv.writes.filter((w) => w.key === KEYS.espn)).toHaveLength(3);
    expect(net.calls.map((c) => JSON.parse(new Headers(c.init?.headers).get("x-fantasy-filter")!).players.filterSlotIds.value)).toEqual([[0, 6], [2, 17, 16], [4]]);
  });

  it("doesn't write when nothing changed", async () => {
    const { deps, kv, clock } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe });
    await runEspn(deps);
    clock.advance(60);
    expect(await runEspn(deps)).toEqual({ result: "unchanged" });
    expect(kv.writes).toHaveLength(1);
    expect(espnStore(kv).fetchedAt).toBe(at(9));
  });

  it("writes, with a new fetchedAt, when a projection moved", async () => {
    const { deps, kv, net, clock } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe });
    await runEspn(deps);
    clock.advance(60);
    const moved = structuredClone(qbTe);
    moved.players[0].player.stats.find((s: { statSourceId: number }) => s.statSourceId === 1).stats["3"] = 260;
    net.routes[ESPN_PROJECTIONS_HOST] = moved;
    expect(await runEspn(deps)).toEqual({ result: "written" });
    expect(espnStore(kv).fetchedAt).toBe("2026-11-19T21:09:00Z");
    expect(espnStore(kv).players["sleeper:4984"]!.statMeans.passing_yards).toBe(260);
  });

  it("still writes the D/STs before the first push has written idmap", async () => {
    const { deps, kv, logs } = setup({ [ESPN_PROJECTIONS_HOST]: rbKDst }, {}, at(29));
    kv.data.delete(KEYS.idmap);
    expect(await runEspn(deps)).toEqual({ result: "written" });
    expect(Object.keys(espnStore(kv).players).every((k) => k.startsWith("dst:"))).toBe(true);
    expect(logs.join("\n")).toMatch(/no idmap yet/);
  });

  // E.g. ESPN numbering a single-digit week's projected line differently from `11${season}${week}`.
  it("warns when it writes no players for its group while Games are Upcoming", async () => {
    const { deps, logs } = setup({ [ESPN_PROJECTIONS_HOST]: { players: [] } });
    expect(await runEspn(deps)).toEqual({ result: "written" });
    expect(logs.join("\n")).toMatch(/warning: 0 players written for QB, TE while 1[0-9]? Games are Upcoming; 0 projections parsed \(line id 11202611\)/);
  });

  it("doesn't warn about an empty group once no Game is Upcoming", async () => {
    const done = structuredClone(upcoming);
    for (const g of done.games) g.status = "final";
    const { deps, logs } = setup({ [ESPN_PROJECTIONS_HOST]: { players: [] } }, { [KEYS.scoreboard]: JSON.stringify(done) });
    await runEspn(deps);
    expect(logs.join("\n")).not.toMatch(/warning/);
  });

  it("does nothing in the postseason: ESPN's fantasy game has no playoff weeks", async () => {
    const { deps, kv, net } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe }, { [KEYS.scoreboard]: JSON.stringify(postseason) });
    expect(await runEspn(deps)).toEqual({ result: "unchanged" });
    expect(net.calls).toHaveLength(0);
    expect(kv.writes).toHaveLength(0);
  });

  it("fails without fetching when the scoreboard run hasn't written yet", async () => {
    const { deps, kv, net } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe });
    kv.data.delete(KEYS.scoreboard);
    const out = await runEspn(deps);
    expect(out.result).toBe("failed");
    expect(out.error).toMatch(/scoreboard/);
    expect(net.calls).toHaveLength(0);
  });

  it.each([
    ["an HTTP error", { status: 503 }, /HTTP 503/],
    ["a network failure", new Error("ECONNRESET"), /ECONNRESET/],
    ["a response of the wrong shape", { players: [{ id: "x" }] }, /EspnProjectionsError/],
  ])("on %s, logs it and keeps the last good store", async (_what, answer, message) => {
    const { deps, kv, net, clock } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe });
    await runEspn(deps);
    const good = kv.data.get(KEYS.espn);
    clock.advance(60);
    net.routes[ESPN_PROJECTIONS_HOST] = answer;
    const out = await runEspn(deps);
    expect(out.result).toBe("failed");
    expect(out.error).toMatch(message);
    expect(kv.data.get(KEYS.espn)).toBe(good);
    expect(kv.writes).toHaveLength(1);
  });

  it("fails, keeping the last good store, on an idmap it can't read", async () => {
    const { deps, kv } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe }, { [KEYS.idmap]: JSON.stringify({ schemaVersion: 1 }) });
    const out = await runEspn(deps);
    expect(out.result).toBe("failed");
    expect(out.error).toMatch(/idmap/);
    expect(kv.writes).toHaveLength(0);
  });

  it("replaces a stored espn it can't read", async () => {
    const { deps, kv } = setup({ [ESPN_PROJECTIONS_HOST]: qbTe }, { [KEYS.espn]: "{not json" });
    expect(await runEspn(deps)).toEqual({ result: "written" });
    expect(espnStore(kv).week).toBe(11);
  });

  it("stays well inside the free plan's KV writes: at most 3 an hour, 72 a day", async () => {
    const { deps, kv, net, clock } = setup({});
    const groups = [qbTe, rbKDst, wr];
    for (let hour = 0; hour < 24; hour++) {
      for (let g = 0; g < 3; g++) {
        // Every projection moves every run.
        const moved = structuredClone(groups[g]);
        for (const p of moved.players) for (const s of p.player.stats) if (s.statSourceId === 1) for (const k in s.stats) s.stats[k] += 0.01 * (hour + 1);
        net.routes[ESPN_PROJECTIONS_HOST] = moved;
        await runEspn(deps);
        clock.advance(20);
      }
    }
    expect(kv.writes.filter((w) => w.key === KEYS.espn).length).toBeLessThanOrEqual(72);
  });
});
