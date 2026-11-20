import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { buildLeaderboard, round2, sameContent, serialize, updatedAtOf } from "../../src/worker/leaderboard.js";
import type { Game, Scoreboard } from "../../src/worker/scoreboard.js";
import { RULES, RULES_NAMES, score } from "../../src/worker/scoring.js";
import type { SleeperOffensePlayer } from "../../src/worker/sleeper.js";
import { readJson } from "../helpers/fakes.js";

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats.default(ajv);
const validate = ajv.compile(readJson("schema/leaderboard.schema.json"));

const game = (over: Partial<Game>): Game => ({
  id: "1",
  kickoff: "2026-11-22T18:00:00Z",
  status: "live",
  home: "KC",
  away: "BUF",
  homeScore: 7,
  awayScore: 3,
  period: 2,
  clock: "7:32",
  ...over,
});

const scoreboard: Scoreboard = {
  week: {
    season: 2026,
    seasonType: "regular",
    number: 12,
    start: "2026-11-25T08:00:00Z",
    end: "2026-12-02T07:59:00Z",
    firstKickoff: "2026-11-22T18:00:00Z",
    lastKickoff: "2026-11-23T01:20:00Z",
  },
  games: [
    game({ id: "10", status: "live", home: "KC", away: "BUF" }),
    game({ id: "11", status: "final", home: "DAL", away: "WAS", homeScore: 20, awayScore: 17, period: null, clock: null }),
    game({ id: "12", status: "upcoming", home: "SF", away: "SEA", homeScore: null, awayScore: null, period: null, clock: null, kickoff: "2026-11-23T01:20:00Z" }),
  ],
};

const player = (over: Partial<SleeperOffensePlayer>): SleeperOffensePlayer => ({
  kind: "offense",
  id: "sleeper:1",
  name: "Pat Doe",
  team: "KC",
  position: "QB",
  sleeperGameId: null,
  stats: {},
  ...over,
});

const UPDATED = "2026-11-22T19:05:00Z";

describe("buildLeaderboard", () => {
  it("fills the top level from the scoreboard, with every Scoring Rules option and no Expected Points yet", () => {
    const lb = buildLeaderboard(scoreboard, [], UPDATED);
    expect(lb).toEqual({
      schemaVersion: 1,
      updatedAt: UPDATED,
      expectedPointsAsOf: null,
      week: scoreboard.week,
      scoringRules: [...RULES_NAMES],
      games: scoreboard.games,
      players: [],
    });
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("scores a Live player's Points So Far under every Scoring Rules option; Week Points are the same for now", () => {
    const stats = { passing_yards: 251, passing_tds: 2, passing_interceptions: 1, rushing_yards: 12, receptions: 0 };
    const lb = buildLeaderboard(scoreboard, [player({ id: "sleeper:9758", stats })], UPDATED);
    const p = lb.players[0]!;
    expect(p).toMatchObject({ id: "sleeper:9758", name: "Pat Doe", team: "KC", position: "QB", gameId: "10", out: false, expectedSource: null });
    expect(Object.keys(p.points)).toEqual([...RULES_NAMES]);
    for (const name of RULES_NAMES) {
      const pts = round2(score(stats, RULES[name]!));
      expect(p.points[name]).toEqual({ week: pts, soFar: pts, expected: null });
    }
    // 251 × 0.04 + 2 × 4 − 2 + 12 × 0.1 = 17.24 under ppr.
    expect(p.points.ppr!.soFar).toBe(17.24);
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("scores a Final player the same way", () => {
    const lb = buildLeaderboard(scoreboard, [player({ team: "WAS", position: "WR", stats: { receptions: 5, receiving_yards: 61 } })], UPDATED);
    expect(lb.players[0]!.gameId).toBe("11");
    expect(lb.players[0]!.points.ppr).toEqual({ week: 11.1, soFar: 11.1, expected: null });
    expect(lb.players[0]!.points.standard).toEqual({ week: 6.1, soFar: 6.1, expected: null });
  });

  it("gives an Upcoming player 0 Week Points and no Points So Far", () => {
    const lb = buildLeaderboard(scoreboard, [player({ team: "SEA" })], UPDATED);
    expect(lb.players[0]!.gameId).toBe("12");
    for (const name of RULES_NAMES) expect(lb.players[0]!.points[name]).toEqual({ week: 0, soFar: null, expected: null });
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("joins by team on either side of the Game and leaves out players on bye", () => {
    const lb = buildLeaderboard(scoreboard, [player({ id: "sleeper:1", team: "BUF" }), player({ id: "sleeper:2", team: "ATL" }), player({ id: "sleeper:3", team: "DAL" })], UPDATED);
    expect(lb.players.map((p) => [p.id, p.gameId])).toEqual([
      ["sleeper:1", "10"],
      ["sleeper:3", "11"],
    ]);
  });

  it("orders players by id, so the same stats in a different row order build the same JSON", () => {
    const a = player({ id: "sleeper:20", stats: { rushing_yards: 1 } });
    const b = player({ id: "sleeper:3", stats: { rushing_yards: 2 } });
    expect(serialize(buildLeaderboard(scoreboard, [a, b], UPDATED))).toBe(serialize(buildLeaderboard(scoreboard, [b, a], UPDATED)));
  });

  it("rounds points to 2 decimals", () => {
    // 0.1 × 3 and 0.04 × 37 carry float noise.
    const lb = buildLeaderboard(scoreboard, [player({ stats: { rushing_yards: 3, passing_yards: 37 } })], UPDATED);
    expect(lb.players[0]!.points.ppr!.soFar).toBe(1.78);
    expect(round2(0.1 * 3)).toBe(0.3);
    expect(round2(-2 + 0.04 * 3)).toBe(-1.88);
    // No "-0" in the JSON.
    expect(Object.is(round2(-0.001), 0)).toBe(true);
  });
});

describe("serialize, updatedAtOf and sameContent", () => {
  const lb = buildLeaderboard(scoreboard, [player({ stats: { rushing_yards: 30 } })], UPDATED);

  it("puts updatedAt where updatedAtOf finds it", () => {
    expect(updatedAtOf(serialize(lb))).toBe(UPDATED);
    expect(updatedAtOf("not json")).toBeNull();
    expect(JSON.parse(serialize(lb))).toEqual(lb);
  });

  it("treats two Leaderboards that differ only in updatedAt as the same content", () => {
    const later = serialize({ ...lb, updatedAt: "2026-11-22T19:10:00Z" });
    expect(sameContent(serialize(lb), later)).toBe(true);
  });

  it("sees any other change", () => {
    const moved = buildLeaderboard(scoreboard, [player({ stats: { rushing_yards: 31 } })], UPDATED);
    expect(sameContent(serialize(lb), serialize(moved))).toBe(false);
    const clock = buildLeaderboard({ ...scoreboard, games: [{ ...scoreboard.games[0]!, clock: "7:01" }, ...scoreboard.games.slice(1)] }, [player({ stats: { rushing_yards: 30 } })], UPDATED);
    expect(sameContent(serialize(lb), serialize(clock))).toBe(false);
  });

  it("treats unreadable stored text as different", () => {
    expect(sameContent("garbage", serialize(lb))).toBe(false);
  });
});
