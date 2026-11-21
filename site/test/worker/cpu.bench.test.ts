/**
 * The stats run's CPU on a full Sunday (S11.6): ~2,000 Sleeper rows, ~1,000 expectations in each of
 * KV `expected` and `espn`, 16 Games mid-slate. Prints each phase's median in Node; Cloudflare's
 * isolates are slower, so `calibration` repeats the S10.1 probe (694 rows parsed and summed: 4–5 ms
 * of CPU on Cloudflare, research/leaderboard-live-spike.md) to scale the numbers.
 *
 *   npx vitest run test/worker/cpu.bench.test.ts --silent=false
 *
 * The assertion is loose, to catch a blow-up (an accidental O(n²)) rather than to time CI machines.
 */
import { describe, expect, it } from "vitest";
import type { EspnStore, ExpectedStore } from "../../src/worker/kv.js";
import { buildLeaderboard, sameContent, serialize } from "../../src/worker/leaderboard.js";
import { readStore } from "../../src/worker/refresh.js";
import { sleeperStatsToPlayers } from "../../src/worker/sleeper.js";
import { fullSunday } from "../helpers/full-sunday.js";

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)]!;
}

function time(runs: number, fn: () => unknown): { first: number; median: number } {
  const times: number[] = [];
  for (let i = 0; i < runs; i++) {
    const t = performance.now();
    fn();
    times.push(performance.now() - t);
  }
  return { first: times[0]!, median: median(times.slice(1)) };
}

const ms = (x: number) => `${x.toFixed(2)} ms`;

describe.each([
  ["full Sunday, ~2,000 rows", 2000],
  ["a recorded full week's size, 694 rows", 694],
])("stats run CPU: %s", (_what, rows) => {
  it("builds and serializes the Leaderboard well inside the budget", () => {
    const generated = fullSunday(rows, 1000);
    // A text of its own per case, so the isolate cache (refresh.ts readStore) starts cold.
    const own = (t: string) => `${t.slice(0, -1)},"bench":${rows}}`;
    const sunday = { ...generated, expectedText: own(generated.expectedText), espnText: own(generated.espnText) };
    const log = () => {};
    const RUNS = 25;

    const parseSleeper = time(RUNS, () => JSON.parse(sunday.sleeperText));
    const raw = JSON.parse(sunday.sleeperText) as unknown[];
    const convert = time(RUNS, () => sleeperStatsToPlayers(raw));
    const players = sleeperStatsToPlayers(raw).players;
    const stores = time(RUNS, () => [
      readStore<ExpectedStore>("expected", sunday.expectedText, "generatedAt", log),
      readStore<EspnStore>("espn", sunday.espnText, "fetchedAt", log),
    ]);
    const storesParse = time(RUNS, () => [JSON.parse(sunday.expectedText), JSON.parse(sunday.espnText)]);
    const expected = readStore<ExpectedStore>("expected", sunday.expectedText, "generatedAt", log);
    const espn = readStore<EspnStore>("espn", sunday.espnText, "fetchedAt", log);
    const build = time(RUNS, () => buildLeaderboard(sunday.scoreboard, players, "2026-11-22T19:05:00Z", { expected, espn }));
    const lb = buildLeaderboard(sunday.scoreboard, players, "2026-11-22T19:05:00Z", { expected, espn });
    const text = serialize(lb);
    const ser = time(RUNS, () => sameContent(serialize(lb), text));

    // The S10.1 probe: parse 694 rows and sum a rough PPR line per row.
    const probe = fullSunday(694, 0).sleeperText;
    const calibration = time(RUNS, () => {
      let sum = 0;
      for (const r of JSON.parse(probe) as { stats: Record<string, number> }[]) sum += (r.stats.rec ?? 0) + (r.stats.rec_yd ?? 0) * 0.1;
      return sum;
    });

    const ours = build.median + ser.median;
    const all = parseSleeper.median + convert.median + stores.first + ours;
    const allCached = all - stores.first + stores.median;
    const scale = 4.5 / calibration.median;
    console.log([
      `${rows} Sleeper rows (${(sunday.sleeperText.length / 1024).toFixed(0)} KB), ${players.length} players, `
        + `KV expected ${(sunday.expectedText.length / 1024).toFixed(0)} KB + espn ${(sunday.espnText.length / 1024).toFixed(0)} KB → `
        + `${lb.players.length} listed, Leaderboard ${(text.length / 1024).toFixed(0)} KB`,
      `  Sleeper JSON.parse      ${ms(parseSleeper.median)} (first ${ms(parseSleeper.first)})`,
      `  sleeperStatsToPlayers   ${ms(convert.median)} (first ${ms(convert.first)})`,
      `  KV stores parse+check   ${ms(stores.first)} when changed (JSON.parse alone ${ms(storesParse.median)}); ${ms(stores.median)} unchanged (isolate cache)`,
      `  buildLeaderboard        ${ms(build.median)} (first ${ms(build.first)})`,
      `  serialize + sameContent ${ms(ser.median)} (first ${ms(ser.first)})`,
      `  build + serialize       ${ms(ours)}; the whole run ${ms(all)} with changed stores, ${ms(allCached)} unchanged`,
      `  calibration (S10.1 probe, 4.5 ms on Cloudflare) ${ms(calibration.median)} → ×${scale.toFixed(1)}: `
        + `build + serialize ≈ ${ms(ours * scale)}, the whole run ≈ ${ms(all * scale)} / ${ms(allCached * scale)} on Cloudflare`,
    ].join("\n"));

    expect(lb.players.length).toBeGreaterThan(0);
    expect(ours).toBeLessThan(100);
  });
});
