import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import type { EspnStore, Expectation, ExpectedStore } from "../../src/worker/kv.js";
import { buildLeaderboard, fractionLeft, round2, sameContent, serialize, updatedAtOf } from "../../src/worker/leaderboard.js";
import type { Game, Scoreboard } from "../../src/worker/scoreboard.js";
import { RULES, RULES_NAMES, score, scoreLine } from "../../src/worker/scoring.js";
import type { SleeperDstPlayer, SleeperKickerPlayer, SleeperOffensePlayer } from "../../src/worker/sleeper.js";
import { buildIndex, rankRows } from "../../src/page/model.js";
import { rowHTML } from "../../src/page/render.js";
import { makeTimeFormat } from "../../src/page/time.js";
import type { Leaderboard as PageLeaderboard } from "../../src/page/types.js";
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

  it("gives an Upcoming player with no Expected Points 0 Week Points and no Points So Far", () => {
    // Listed only because his line isn't empty (a stat correction from an earlier week, say).
    const lb = buildLeaderboard(scoreboard, [player({ team: "SEA", stats: { receptions: 0 } })], UPDATED);
    expect(lb.players[0]!.gameId).toBe("12");
    for (const name of RULES_NAMES) expect(lb.players[0]!.points[name]).toEqual({ week: 0, soFar: null, expected: null });
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("joins by team on either side of the Game and leaves out players on bye", () => {
    const stats = { receptions: 1 };
    const lb = buildLeaderboard(scoreboard, [player({ id: "sleeper:1", team: "BUF", stats }), player({ id: "sleeper:2", team: "ATL", stats }), player({ id: "sleeper:3", team: "DAL", stats })], UPDATED);
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

// ---- Sprint 11: Expected Points, ESPN projections and the Projected Total (S11.6) ----

const WEEK = { season: 2026, seasonType: "regular" as const, week: 12 };

const expectation = (over: Partial<Expectation> = {}): Expectation => ({
  name: "Pat Doe",
  team: "KC",
  position: "QB",
  kickoff: "2026-11-22T18:00:00Z",
  statMeans: { passing_yards: 250, passing_tds: 1.5 },
  expected: { ppr: 16, "half-ppr": 16, standard: 16, "espn-league": 13 },
  ...over,
});

const expectedStore = (players: Record<string, Expectation>, over: Partial<ExpectedStore> = {}): ExpectedStore => ({
  schemaVersion: 1,
  ...WEEK,
  generatedAt: "2026-11-22T17:50:00Z",
  players,
  ...over,
});

const espnStore = (players: Record<string, Expectation>, over: Partial<EspnStore> = {}): EspnStore => ({
  schemaVersion: 1,
  ...WEEK,
  fetchedAt: "2026-11-22T17:40:00Z",
  players,
  ...over,
});

const kicker = (over: Partial<SleeperKickerPlayer>): SleeperKickerPlayer => ({
  kind: "kicker", id: "sleeper:50", name: "Kay Kicker", team: "KC", position: "K", sleeperGameId: null, stats: {}, ...over,
});
const dst = (over: Partial<SleeperDstPlayer>): SleeperDstPlayer => ({
  kind: "dst", id: "dst:KC", name: "Chiefs D/ST", team: "KC", position: "D/ST", sleeperGameId: null, stats: {}, ...over,
});

/** The scoreboard with Game 10 (KC–BUF) Live at a chosen point. */
const liveAt = (period: number, clock: string): Scoreboard => ({
  ...scoreboard,
  games: [{ ...scoreboard.games[0]!, period, clock }, ...scoreboard.games.slice(1)],
});

describe("fractionLeft", () => {
  it.each([
    [1, "15:00", 1],
    [1, "7:30", (450 + 2700) / 3600],
    [2, "7:30", (450 + 1800) / 3600],
    [2, "0:00", 0.5], // halftime: ESPN shows Q2 0:00
    [3, "0:00", 0.25], // end of the third quarter
    [4, "2:00", 120 / 3600],
    [4, "0:00", 0], // end of regulation, before overtime or Final
    [5, "10:00", 0], // overtime counts as nothing left
    [6, "3:00", 0],
  ])("Q%i %s → %f", (period, clock, f) => {
    expect(fractionLeft({ period, clock })).toBeCloseTo(f, 10);
  });

  it("clamps to 0..1 and survives a missing or odd clock", () => {
    expect(fractionLeft({ period: 1, clock: "16:30" })).toBe(1);
    expect(fractionLeft({ period: 0, clock: "15:00" })).toBe(1);
    // No period: the Game has only just gone Live.
    expect(fractionLeft({ period: null, clock: null })).toBe(1);
    // A period but no readable clock: the quarter counts as played out.
    expect(fractionLeft({ period: 2, clock: null })).toBe(0.5);
    expect(fractionLeft({ period: 2, clock: "soon" })).toBe(0.5);
  });
});

describe("buildLeaderboard with Expected Points", () => {
  it("takes Expected Points from ffp first, then ESPN's projection, else none, and expectedPointsAsOf from the ffp store", () => {
    const stats = { rushing_yards: 10 };
    const players = [player({ id: "sleeper:1", stats }), player({ id: "sleeper:2", stats }), player({ id: "sleeper:3", stats })];
    const lb = buildLeaderboard(scoreboard, players, UPDATED, {
      expected: expectedStore({ "sleeper:1": expectation() }),
      espn: espnStore({ "sleeper:1": expectation({ expected: { ppr: 3 } }), "sleeper:2": expectation({ expected: { ppr: 9, "half-ppr": 9, standard: 9, "espn-league": 9 } }) }),
    });
    expect(lb.expectedPointsAsOf).toBe("2026-11-22T17:50:00Z");
    expect(lb.players.map((p) => [p.id, p.expectedSource, p.points.ppr!.expected])).toEqual([
      ["sleeper:1", "ffp", 16],
      ["sleeper:2", "espn", 9],
      ["sleeper:3", null, null],
    ]);
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("has no expectedPointsAsOf with only ESPN's projections", () => {
    const lb = buildLeaderboard(scoreboard, [], UPDATED, { expected: null, espn: espnStore({}) });
    expect(lb.expectedPointsAsOf).toBeNull();
  });

  it.each([
    ["season", { season: 2025 }],
    ["season type", { seasonType: "postseason" as const }],
    ["week", { week: 11 }],
  ])("ignores a store for another %s than the scoreboard's", (_what, over) => {
    const exp = { "sleeper:1": expectation({ team: "SEA" }) };
    const lb = buildLeaderboard(scoreboard, [player({ id: "sleeper:1", team: "SEA", stats: { receptions: 1 } })], UPDATED, {
      expected: expectedStore(exp, over),
      espn: espnStore(exp, over),
    });
    expect(lb.expectedPointsAsOf).toBeNull();
    expect(lb.players[0]).toMatchObject({ expectedSource: null });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 0, soFar: null, expected: null });
  });

  it("lists a player with Expected Points of 1 or more who has no Sleeper row, from his expectation", () => {
    const lb = buildLeaderboard(scoreboard, [], UPDATED, {
      expected: expectedStore({
        "sleeper:7": expectation({ name: "Sam Seahawk", team: "SEA", position: "WR", expected: { ppr: 12.345, "half-ppr": 10, standard: 8, "espn-league": 12 } }),
        "dst:SF": expectation({ name: "49ers D/ST", team: "SF", position: "D/ST", statMeans: {}, expected: { ppr: 7, "half-ppr": 7, standard: 7, "espn-league": 6 } }),
        "sleeper:8": expectation({ team: "SEA", expected: { ppr: 0.9, "half-ppr": 0.9, standard: 0.9, "espn-league": 0.9 } }),
        "sleeper:9": expectation({ team: "ATL" }), // on bye
      }),
      espn: null,
    });
    expect(lb.players.map((p) => p.id)).toEqual(["dst:SF", "sleeper:7"]);
    const sam = lb.players[1]!;
    expect(sam).toMatchObject({ name: "Sam Seahawk", team: "SEA", position: "WR", gameId: "12", out: false, expectedSource: "ffp" });
    // Upcoming: Week Points are Expected Points.
    expect(sam.points.ppr).toEqual({ week: 12.35, soFar: null, expected: 12.35 });
    expect(sam.points.standard).toEqual({ week: 8, soFar: null, expected: 8 });
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("lists a Sleeper player once he has a stat line, or Expected Points of 1 or more; an empty line alone isn't enough", () => {
    const lb = buildLeaderboard(scoreboard, [
      player({ id: "sleeper:1", team: "DAL", stats: {} }), // Final, nothing recorded, no expectation
      player({ id: "sleeper:2", team: "DAL", stats: { receptions: 0 } }), // a recorded 0 is a stat
      player({ id: "sleeper:3", team: "DAL", stats: {} }), // nothing recorded, but expected to score
      player({ id: "sleeper:4", team: "SEA", stats: {} }), // Upcoming, no expectation
      player({ id: "sleeper:5", team: "DAL", stats: {} }), // Expected Points under 1
    ], UPDATED, {
      expected: expectedStore({
        "sleeper:3": expectation({ team: "DAL" }),
        "sleeper:5": expectation({ team: "DAL", expected: { ppr: 0.5, "half-ppr": 0.5, standard: 0.5, "espn-league": 0.5 } }),
      }),
      espn: null,
    });
    expect(lb.players.map((p) => p.id)).toEqual(["sleeper:2", "sleeper:3"]);
    // Final: Week Points are Points So Far; Expected Points stay as frozen.
    expect(lb.players[1]!.points.ppr).toEqual({ week: 0, soFar: 0, expected: 16 });
  });

  it("joins a Sleeper player to his Game by his Sleeper team, and takes name and position from Sleeper", () => {
    const lb = buildLeaderboard(scoreboard, [player({ id: "sleeper:1", name: "Pat Doe Jr.", team: "DAL", stats: { receptions: 2 } })], UPDATED, {
      expected: expectedStore({ "sleeper:1": expectation({ name: "Pat Doe", team: "SEA" }) }),
      espn: null,
    });
    expect(lb.players[0]).toMatchObject({ name: "Pat Doe Jr.", team: "DAL", gameId: "11", expectedSource: "ffp" });
  });

  it("Live: projects each Scoring Stat to the end of the Game, then scores it", () => {
    // Q3 0:00: a quarter left, f = 0.25.
    const lb = buildLeaderboard(liveAt(3, "0:00"), [player({ id: "sleeper:1", stats: { passing_yards: 200, passing_tds: 1 } })], UPDATED, {
      expected: expectedStore({ "sleeper:1": expectation({ statMeans: { passing_yards: 240, passing_tds: 2, rushing_yards: 20 } }) }),
      espn: null,
    });
    const p = lb.players[0]!;
    // So far: 200 × 0.04 + 4 = 12. Projected: 260 yards, 1.5 TDs, 5 rush yards → 10.4 + 6 + 0.5 = 16.9.
    expect(p.points.ppr).toEqual({ week: 16.9, soFar: 12, expected: 16 });
    expect(p.points["espn-league"]!.expected).toBe(13);
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("Live without an expectation: Week Points are Points So Far", () => {
    const lb = buildLeaderboard(liveAt(1, "10:00"), [player({ stats: { passing_yards: 100 } })], UPDATED, { expected: null, espn: null });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 4, soFar: 4, expected: null });
  });

  it("Live in overtime: nothing left, so Week Points are Points So Far", () => {
    const lb = buildLeaderboard(liveAt(5, "8:00"), [player({ stats: { passing_yards: 100 } })], UPDATED, {
      expected: expectedStore({ "sleeper:1": expectation() }),
      espn: null,
    });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 4, soFar: 4, expected: 16 });
  });

  it("Live with an expectation but no Sleeper row yet: Points So Far 0 and the projection from 0", () => {
    const lb = buildLeaderboard(liveAt(2, "0:00"), [], UPDATED, {
      expected: null,
      espn: espnStore({ "sleeper:1": expectation({ statMeans: { receptions: 4, receiving_yards: 50 }, position: "WR" }) }),
    });
    // Halftime: half of 4 catches and 50 yards → 2 + 2.5.
    expect(lb.players[0]).toMatchObject({ id: "sleeper:1", expectedSource: "espn", position: "WR" });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 4.5, soFar: 0, expected: 16 });
  });

  it("Live kicker: FGs made project by Sleeper's distance buckets, each scored at its bucket", () => {
    const lb = buildLeaderboard(liveAt(2, "0:00"), [kicker({ stats: { pat_made: 1, fgm_40_49: 1 } })], UPDATED, {
      expected: expectedStore({
        "sleeper:50": expectation({ position: "K", statMeans: { pat_made: 2, fgm_40_49: 0.5, fgm_50_59: 0.4, fg_missed: 0.2 }, expected: { ppr: 8, "half-ppr": 8, standard: 8, "espn-league": 8.5 } }),
      }),
      espn: null,
    });
    // So far: 1 + 4 = 5. Projected (f = 0.5): 2 PATs, 1.25 × 4, 0.2 × 5, 0.1 × −1 → 2 + 5 + 1 − 0.1 = 7.9.
    expect(lb.players[0]!.points.ppr).toEqual({ week: 7.9, soFar: 5, expected: 8 });
  });

  it("Live kicker with no Sleeper row: scored as a kicker from his expectation's position", () => {
    const lb = buildLeaderboard(liveAt(2, "0:00"), [], UPDATED, {
      expected: expectedStore({ "sleeper:50": expectation({ position: "K", statMeans: { pat_made: 2, fgm_50_59: 1 } }) }),
      espn: null,
    });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 3.5, soFar: 0, expected: 16 });
  });

  it("Live D/ST: Points and Yards Allowed project to the end of the Game before the tiers", () => {
    const stats = { sacks: 2, points_allowed_all: 6, yards_allowed: 150 };
    const lb = buildLeaderboard(liveAt(2, "0:00"), [dst({ stats })], UPDATED, {
      expected: expectedStore({
        "dst:KC": expectation({ name: "Chiefs D/ST", position: "D/ST", statMeans: { sacks: 2, points_allowed_all: 20, yards_allowed: 320 }, expected: { ppr: 6, "half-ppr": 6, standard: 6, "espn-league": 5 } }),
      }),
      espn: null,
    });
    // So far: 2 sacks + PA 6 (4) + YA 150 (3) = 9. Projected: 3 sacks + PA 16 (1) + YA 310 (0) = 4.
    // Prorating points instead would give 9 + 6 × 0.5 = 12: the tiers matter.
    expect(round2(scoreLine({ kind: "dst", stats: { sacks: 3, points_allowed_all: 16, yards_allowed: 310 } }, RULES.ppr!))).toBe(4);
    // The tier part is blended with the expected tier points by the fraction left: expected tier
    // points are Expected Points less the per-unit means (6 − 2 sacks = 4), so 3 + 0.5 × 1 + 0.5 × 4.
    expect(lb.players[0]!.points.ppr).toEqual({ week: 5.5, soFar: 9, expected: 6 });
    expect(validate(lb), JSON.stringify(validate.errors)).toBe(true);
  });

  it("Live D/ST at kickoff: the Projected Total equals Expected Points, not the tier of the mean", () => {
    // Points Allowed 12.5 on average is in the 7–13 tier (3 under ppr), but the expected tier points
    // are much lower when the spread of outcomes is wide; Expected Points carry that.
    const lb = buildLeaderboard(liveAt(1, "15:00"), [dst({ stats: {} })], UPDATED, {
      expected: expectedStore({
        "dst:KC": expectation({ name: "Chiefs D/ST", position: "D/ST", statMeans: { sacks: 2.5, points_allowed_all: 12.5, yards_allowed: 330 }, expected: { ppr: 4.1, "half-ppr": 4.1, standard: 4.1, "espn-league": 3.6 } }),
      }),
      espn: null,
    });
    for (const name of RULES_NAMES) expect(lb.players[0]!.points[name]!.week, name).toBeCloseTo(lb.players[0]!.points[name]!.expected!, 6);
  });

  it("Final: Week Points are Points So Far, with the frozen Expected Points beside them", () => {
    const lb = buildLeaderboard(scoreboard, [player({ team: "WAS", stats: { receptions: 5, receiving_yards: 61 } })], UPDATED, {
      expected: null,
      espn: espnStore({ "sleeper:1": expectation({ team: "WAS" }) }),
    });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 11.1, soFar: 11.1, expected: 16 });
    expect(lb.players[0]!.expectedSource).toBe("espn");
  });

  it("Upcoming with a Sleeper row and an expectation: Week Points are Expected Points", () => {
    const lb = buildLeaderboard(scoreboard, [player({ team: "SEA", stats: {} })], UPDATED, {
      expected: expectedStore({ "sleeper:1": expectation({ team: "SEA" }) }),
      espn: null,
    });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 16, soFar: null, expected: 16 });
  });

  it("leaves `out` false: nothing in Sleeper's rows or the scoreboard says a player was ruled out", () => {
    const lb = buildLeaderboard(scoreboard, [player({ stats: { receptions: 1 } })], UPDATED, { expected: expectedStore({ "sleeper:1": expectation() }), espn: null });
    expect(lb.players[0]!.out).toBe(false);
  });

  it("reads on the page as intended: Upcoming ranks by Expected Points, the ESPN note, the banked bar", () => {
    const lb = buildLeaderboard(liveAt(2, "0:00"), [player({ id: "sleeper:1", stats: { passing_yards: 100 } })], UPDATED, {
      expected: expectedStore({ "sleeper:1": expectation(), "sleeper:2": expectation({ name: "Up Coming", team: "SEA", expected: { ppr: 20 } }) }),
      espn: espnStore({ "sleeper:3": expectation({ name: "Espn Guy", team: "SF", expected: { ppr: 3 } }) }),
    });
    const board = JSON.parse(serialize(lb)) as PageLeaderboard;
    const rows = rankRows(buildIndex(board), "ppr");
    expect(rows.map((r) => [r.player.name, r.rank, r.points.week])).toEqual([
      ["Up Coming", 1, 20],
      ["Pat Doe", 2, 12], // 4 so far + half of 250 yards and 1.5 TDs: 4 + 5 + 3
      ["Espn Guy", 3, 3],
    ]);
    const fmt = makeTimeFormat({ locale: "en-US", timeZone: "America/New_York" });
    expect(rowHTML(rows[1]!, fmt)).toContain("scaleX(0.333)"); // 4 of 12 banked
    expect(rowHTML(rows[2]!, fmt)).toContain("ESPN projection");
    expect(rowHTML(rows[0]!, fmt)).not.toContain('class="none"');
  });

  it("gives an expectation missing a Scoring Rules option no Expected Points under it", () => {
    const lb = buildLeaderboard(scoreboard, [], UPDATED, {
      expected: expectedStore({ "sleeper:1": expectation({ team: "SEA", expected: { ppr: 5 } }) }),
      espn: null,
    });
    expect(lb.players[0]!.points.ppr).toEqual({ week: 5, soFar: null, expected: 5 });
    expect(lb.players[0]!.points.standard).toEqual({ week: 0, soFar: null, expected: null });
  });
});
