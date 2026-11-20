import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { serialize, updatedAtOf, type Leaderboard } from "../../src/worker/leaderboard.js";
import {
  HEARTBEAT_MINUTES,
  KEYS,
  runScoreboard,
  runStats,
  SCOREBOARD_URL,
  sleeperStatsUrl,
  USER_AGENT,
  type RunDeps,
} from "../../src/worker/refresh.js";
import { parseScoreboard } from "../../src/worker/scoreboard.js";
import { fakeClock, fakeFetch, FakeKV, readJson, type Route } from "../helpers/fakes.js";

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats.default(ajv);
const validate = ajv.compile(readJson("schema/leaderboard.schema.json"));

// The same Thursday Game (IND @ HOU, Week 11, Q3) from both sources (S9.1).
const espnLive = readJson("test/fixtures/espn/scoreboard-live.json");
const espnFinal = readJson("test/fixtures/espn/scoreboard-final.json");
const espnPost = readJson("test/fixtures/espn/scoreboard-postseason.json");
const sleeperLive: unknown[] = readJson("test/fixtures/sleeper/stats-live.json");

const SLEEPER = "https://api.sleeper.com/stats/nfl/";

function setup(routes: Record<string, Route>, kv = new FakeKV(), start = "2026-11-20T02:45:00Z") {
  const net = fakeFetch(routes);
  const clock = fakeClock(start);
  const logs: string[] = [];
  const deps: RunDeps = { fetch: net.fetch, kv, now: clock.now, log: (m) => logs.push(m) };
  return { deps, kv, net, clock, logs };
}

const stored = (kv: FakeKV): Leaderboard => JSON.parse(kv.data.get(KEYS.leaderboard)!);

describe("scoreboard run", () => {
  it("fetches ESPN's scoreboard with our User-Agent and a timeout, and writes the parsed scoreboard", async () => {
    const { deps, kv, net } = setup({ [SCOREBOARD_URL]: espnLive });
    expect(await runScoreboard(deps)).toEqual({ result: "written" });
    expect(net.calls).toHaveLength(1);
    expect(net.calls[0]!.url).toBe("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard");
    expect(new Headers(net.calls[0]!.init?.headers).get("user-agent")).toBe(USER_AGENT);
    expect(USER_AGENT).toMatch(/^ffp-leaderboard\/0\.1 \(non-commercial/);
    expect(net.calls[0]!.init?.signal).toBeInstanceOf(AbortSignal);
    expect(JSON.parse(kv.data.get(KEYS.scoreboard)!)).toEqual(parseScoreboard(espnLive));
  });

  it("doesn't write when nothing changed, and writes when it did", async () => {
    const { deps, kv, net } = setup({ [SCOREBOARD_URL]: espnLive });
    await runScoreboard(deps);
    expect(await runScoreboard(deps)).toEqual({ result: "unchanged" });
    expect(kv.writes).toHaveLength(1);
    net.routes[SCOREBOARD_URL] = espnFinal;
    expect(await runScoreboard(deps)).toEqual({ result: "written" });
    expect(kv.writes).toHaveLength(2);
    expect(JSON.parse(kv.data.get(KEYS.scoreboard)!)).toEqual(parseScoreboard(espnFinal));
  });

  it.each([
    ["an HTTP error", { status: 403 }, /HTTP 403/],
    ["a network failure", new Error("connection reset"), /connection reset/],
    ["a response of the wrong shape", { events: "nope" }, /ESPN scoreboard/],
    ["a week the Leaderboard doesn't cover", { ...espnLive, season: { year: 2026, type: 1 } }, /neither regular season/],
  ])("on %s, logs it and keeps the last good scoreboard", async (_what, answer, message) => {
    const good = JSON.stringify(parseScoreboard(espnLive));
    const { deps, kv, logs } = setup({ [SCOREBOARD_URL]: answer }, new FakeKV({ [KEYS.scoreboard]: good }));
    const out = await runScoreboard(deps);
    expect(out.result).toBe("failed");
    expect(out.error).toMatch(message);
    expect(logs.join("\n")).toMatch(message);
    expect(kv.writes).toHaveLength(0);
    expect(kv.data.get(KEYS.scoreboard)).toBe(good);
  });

  it("gives up on a request that outlasts the timeout", async () => {
    const kv = new FakeKV();
    const hang = (_: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(init.signal!.reason)));
    const out = await runScoreboard({ fetch: hang as typeof fetch, kv, now: () => new Date(), log: () => {}, timeoutMs: 20 });
    expect(out.result).toBe("failed");
    expect(out.error).toMatch(/timed out|timeout|abort/i);
    expect(kv.writes).toHaveLength(0);
  });
});

describe("sleeperStatsUrl", () => {
  it("asks for the scoreboard's season and week, every Leaderboard position", () => {
    expect(sleeperStatsUrl(parseScoreboard(espnLive).week)).toBe(
      "https://api.sleeper.com/stats/nfl/2026/11?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF",
    );
  });

  it("uses season_type=post in the postseason", () => {
    const week = parseScoreboard(espnPost).week;
    expect(sleeperStatsUrl(week)).toBe(
      `https://api.sleeper.com/stats/nfl/${week.season}/${week.number}?season_type=post&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF`,
    );
  });
});

describe("stats run", () => {
  const withScoreboard = (espn: unknown = espnLive) => new FakeKV({ [KEYS.scoreboard]: JSON.stringify(parseScoreboard(espn)) });

  it("end to end on the Thursday fixtures: cron → KV → a Leaderboard that passes the schema", async () => {
    const { deps, kv, net } = setup({ [SCOREBOARD_URL]: espnLive, [SLEEPER]: sleeperLive });
    expect(await runScoreboard(deps)).toEqual({ result: "written" });
    expect(await runStats(deps)).toEqual({ result: "written" });

    expect(net.calls.map((c) => c.url)).toEqual([SCOREBOARD_URL, sleeperStatsUrl(parseScoreboard(espnLive).week)]);
    expect(new Headers(net.calls[1]!.init?.headers).get("user-agent")).toBe(USER_AGENT);
    expect(net.calls[1]!.init?.signal).toBeInstanceOf(AbortSignal);

    const lb = stored(kv);
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
    expect(lb.updatedAt).toBe("2026-11-20T02:45:00Z");
    expect(lb.week.number).toBe(11);
    expect(lb.games).toHaveLength(13);
    // Every QB/RB/WR/TE row that has recorded a Scoring Stat is in the Live Game. With no Expected
    // Points in KV, the 30 rows with only gms_active-style keys (backups who haven't touched the
    // ball) aren't listed. K and DEF rows come with S11.1.
    const offense = sleeperLive.filter((r) => ["QB", "RB", "WR", "TE"].includes((r as { player: { position: string } }).player.position));
    expect(offense).toHaveLength(46);
    expect(lb.players).toHaveLength(16);
    expect(new Set(lb.players.map((p) => p.gameId))).toEqual(new Set(["401873066"]));
    // C.J. Stroud: 134 pass yards, 2 pass TDs, 1 INT, 4 rush yards. Sleeper's pts_ppr is 12.76 with
    // −1 per INT; ours takes −2.
    const stroud = lb.players.find((p) => p.id === "sleeper:9758")!;
    expect(stroud).toMatchObject({ name: "C.J. Stroud", team: "HOU", position: "QB", out: false, expectedSource: null });
    expect(stroud.points.ppr).toEqual({ week: 11.76, soFar: 11.76, expected: null });
    expect(Object.keys(stroud.points)).toEqual(lb.scoringRules);
  });

  it("gives players of an Upcoming Game 0 Week Points without Expected Points, and leaves out teams on bye", async () => {
    const row = (id: string, team: string) => ({ player_id: id, team, stats: { rec: 0 }, player: { first_name: "A", last_name: id, position: "WR" } });
    // KC plays later in Week 11; ATL is on bye.
    const { deps, kv } = setup({ [SLEEPER]: [...sleeperLive, row("1", "KC"), row("2", "ATL")] }, withScoreboard());
    await runStats(deps);
    const lb = stored(kv);
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
    expect(lb.players.find((p) => p.id === "sleeper:1")!.points.ppr).toEqual({ week: 0, soFar: null, expected: null });
    expect(lb.players.find((p) => p.id === "sleeper:2")).toBeUndefined();
  });

  it("fails without writing when the scoreboard run hasn't written yet", async () => {
    const { deps, kv, net } = setup({ [SLEEPER]: sleeperLive });
    const out = await runStats(deps);
    expect(out.result).toBe("failed");
    expect(out.error).toMatch(/scoreboard/);
    expect(net.calls).toHaveLength(0);
    expect(kv.writes).toHaveLength(0);
  });

  it.each([
    ["an HTTP error", { status: 429 }, /HTTP 429/],
    ["a network failure", new Error("ECONNRESET"), /ECONNRESET/],
    ["a response of the wrong shape", [{ player_id: "1" }], /Sleeper stats/],
  ])("on %s, logs it and keeps the last good Leaderboard", async (_what, answer, message) => {
    const { deps, kv } = setup({ [SLEEPER]: sleeperLive }, withScoreboard());
    await runStats(deps);
    const good = kv.data.get(KEYS.leaderboard);
    const bad = setup({ [SLEEPER]: answer }, kv);
    const out = await runStats(bad.deps);
    expect(out.result).toBe("failed");
    expect(out.error).toMatch(message);
    expect(bad.logs.join("\n")).toMatch(message);
    expect(kv.data.get(KEYS.leaderboard)).toBe(good);
    expect(kv.writes).toHaveLength(1);
  });

  it("writes, with a new updatedAt, whenever anything but updatedAt changed", async () => {
    const { deps, kv, net, clock } = setup({ [SLEEPER]: sleeperLive }, withScoreboard());
    await runStats(deps);
    clock.advance(5);
    const moved = structuredClone(sleeperLive) as { player_id: string; stats: Record<string, number> }[];
    moved.find((r) => r.player_id === "9758")!.stats.pass_yd = 150;
    net.routes[SLEEPER] = moved;
    expect(await runStats(deps)).toEqual({ result: "written" });
    expect(kv.writes).toHaveLength(2);
    expect(stored(kv).updatedAt).toBe("2026-11-20T02:50:00Z");
  });

  it(`skips the write when nothing changed, but refreshes updatedAt once it is ${HEARTBEAT_MINUTES} minutes old`, async () => {
    const { deps, kv, clock } = setup({ [SLEEPER]: sleeperLive }, withScoreboard());
    await runStats(deps);
    const first = kv.data.get(KEYS.leaderboard)!;
    clock.advance(5);
    expect(await runStats(deps)).toEqual({ result: "unchanged" });
    expect(kv.writes).toHaveLength(1);
    clock.advance(HEARTBEAT_MINUTES - 5);
    expect(await runStats(deps)).toEqual({ result: "written" });
    expect(kv.writes).toHaveLength(2);
    const second = kv.data.get(KEYS.leaderboard)!;
    expect(updatedAtOf(second)).toBe("2026-11-20T02:55:00Z");
    expect(second.replace(updatedAtOf(second)!, "")).toBe(first.replace(updatedAtOf(first)!, ""));
  });

  it("stays inside the free plan's 1,000 KV writes a day, even if both keys change on every run", async () => {
    // A day of runs at worst: the scoreboard changes every run (a Live clock) and so does the Leaderboard.
    const kv = new FakeKV();
    const clock = fakeClock("2026-11-22T00:00:00Z");
    let n = 0;
    const net = fakeFetch({});
    const deps: RunDeps = { fetch: net.fetch, kv, now: clock.now, log: () => {} };
    for (let run = 0; run < 288; run++) {
      const espn = structuredClone(espnLive);
      espn.events[0].status.displayClock = `${n % 15}:${String(n % 60).padStart(2, "0")}`;
      n++;
      net.routes[SCOREBOARD_URL] = espn;
      net.routes[SLEEPER] = sleeperLive;
      await runScoreboard(deps);
      await runStats(deps);
      clock.advance(5);
    }
    expect(kv.writes.length).toBeLessThanOrEqual(2 * 288);
    expect(kv.writes.length).toBeLessThan(1000);
  });

  it("writes the Leaderboard as serialize() lays it out, so updatedAtOf can read it", async () => {
    const { deps, kv } = setup({ [SLEEPER]: sleeperLive }, withScoreboard());
    await runStats(deps);
    const text = kv.data.get(KEYS.leaderboard)!;
    expect(text).toBe(serialize(JSON.parse(text)));
  });
});
