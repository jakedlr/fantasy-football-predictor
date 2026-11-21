import { describe, expect, it } from "vitest";
import { invertIdMap, mergeExpected, sameIdMap } from "../../src/push/merge.js";
import type { Push, PushPlayer } from "../../src/push/validate.js";
import type { ExpectedStore } from "../../src/worker/kv.js";
import { readJson } from "../helpers/fakes.js";

const example = (): Push => readJson("schema/examples/expected-points.json");

function player(over: Partial<PushPlayer>): PushPlayer {
  return {
    sleeperId: "100",
    gsisId: "00-0000100",
    espnId: "9100",
    name: "Example Back",
    team: "KC",
    position: "RB",
    kickoff: "2026-11-22T21:25:00Z",
    statMeans: { rushing_yards: 60 },
    expected: { ppr: 10, "half-ppr": 9, standard: 8, "espn-league": 10 },
    ...over,
  };
}

const entry = ({ name, team, position, kickoff, statMeans, expected }: PushPlayer) => ({ name, team, position, kickoff, statMeans, expected });

describe("mergeExpected", () => {
  it("stores a first push keyed by Leaderboard id, without the push-only ids", () => {
    const push = example();
    const store = mergeExpected(null, push);
    expect(store).toEqual({
      schemaVersion: 1,
      season: 2026,
      seasonType: "regular",
      week: 12,
      generatedAt: push.generatedAt,
      players: {
        "sleeper:11635": entry(push.players[0]!),
        "sleeper:6789": entry(push.players[1]!),
        "dst:BAL": entry(push.players[2]!),
      },
    } satisfies ExpectedStore);
  });

  it("keeps a stored entry whose Game kicked off at or before the push, and takes the push for the rest", () => {
    const early = player({ sleeperId: "1", kickoff: "2026-11-22T18:00:00Z", expected: { ppr: 1, "half-ppr": 1, standard: 1, "espn-league": 1 } });
    const onTheDot = player({ sleeperId: "2", kickoff: "2026-11-22T21:25:00Z" });
    const late = player({ sleeperId: "3", kickoff: "2026-11-23T01:20:00Z" });
    const first = mergeExpected(null, { ...example(), generatedAt: "2026-11-22T17:30:00Z", players: [early, onTheDot, late] });

    const changed = { ppr: 20, "half-ppr": 19, standard: 18, "espn-league": 20 };
    const second = mergeExpected(first, {
      ...example(),
      generatedAt: "2026-11-22T21:25:00Z",
      // A late push still listing the early player must not thaw him.
      players: [{ ...early, expected: changed }, { ...onTheDot, expected: changed }, { ...late, expected: changed }],
    });
    expect(second.generatedAt).toBe("2026-11-22T21:25:00Z");
    expect(second.players["sleeper:1"]).toEqual(first.players["sleeper:1"]);
    expect(second.players["sleeper:2"]).toEqual(first.players["sleeper:2"]);
    expect(second.players["sleeper:3"]!.expected).toEqual(changed);
  });

  it("keeps a frozen player the push leaves out, and drops one whose Game hasn't kicked off", () => {
    const kicked = player({ sleeperId: "1", kickoff: "2026-11-22T18:00:00Z" });
    const ruledOut = player({ sleeperId: "2", kickoff: "2026-11-22T21:25:00Z" });
    const stays = player({ sleeperId: "3", kickoff: "2026-11-22T21:25:00Z" });
    const first = mergeExpected(null, { ...example(), generatedAt: "2026-11-22T17:30:00Z", players: [kicked, ruledOut, stays] });
    const second = mergeExpected(first, { ...example(), generatedAt: "2026-11-22T18:30:00Z", players: [stays] });
    expect(Object.keys(second.players).sort()).toEqual(["sleeper:1", "sleeper:3"]);
  });

  it.each([
    ["week", { week: 13 }],
    ["season", { season: 2027 }],
    ["seasonType", { seasonType: "postseason" as const, week: 1 }],
  ])("replaces the store for another %s, frozen players included", (_what, over) => {
    const kicked = player({ sleeperId: "1", kickoff: "2026-11-22T18:00:00Z" });
    const first = mergeExpected(null, { ...example(), generatedAt: "2026-11-22T17:30:00Z", players: [kicked] });
    const next = player({ sleeperId: "9", kickoff: "2026-11-29T18:00:00Z" });
    const second = mergeExpected(first, { ...example(), ...over, generatedAt: "2026-11-25T12:00:00Z", players: [next] });
    expect(Object.keys(second.players)).toEqual(["sleeper:9"]);
    expect(second).toMatchObject(over);
  });
});

describe("invertIdMap", () => {
  it("maps ESPN id to Sleeper id", () => {
    expect(invertIdMap({ "11635": "4361370", "6789": "4360000" })).toEqual({ "4361370": "11635", "4360000": "6789" });
  });

  it("leaves out an ESPN id that two Sleeper ids claim, rather than guess", () => {
    expect(invertIdMap({ "1": "500", "2": "500", "3": "600" })).toEqual({ "600": "3" });
  });
});

describe("sameIdMap", () => {
  it("compares by content, not key order", () => {
    expect(sameIdMap({ a: "1", b: "2" }, { b: "2", a: "1" })).toBe(true);
    expect(sameIdMap({ a: "1" }, { a: "1", b: "2" })).toBe(false);
    expect(sameIdMap({ a: "1", b: "2" }, { a: "1", c: "2" })).toBe(false);
    expect(sameIdMap({ a: "1" }, { a: "2" })).toBe(false);
  });
});
