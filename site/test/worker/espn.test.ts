import { describe, expect, it } from "vitest";
import {
  buildEspnStore,
  ESPN_PROJECTIONS_HOST,
  EspnProjectionsError,
  espnExpected,
  espnStatMeans,
  groupAt,
  parseProjections,
  PROJECTION_GROUPS,
  projectionsRequest,
  type EspnProjection,
} from "../../src/worker/espn.js";
import type { EspnStore, IdMapStore } from "../../src/worker/kv.js";
import { parseScoreboard, type Scoreboard } from "../../src/worker/scoreboard.js";
import { RULES, RULES_NAMES, tierPoints } from "../../src/worker/scoring.js";
import { readJson } from "../helpers/fakes.js";

// Recorded 2026-11-20 (Week 11, Friday), trimmed by the S11.5 spike: research/leaderboard-live-spike.md.
const qbTe = readJson("test/fixtures/espn/projections-qb-te.json");
const rbKDst = readJson("test/fixtures/espn/projections-rb-k-dst.json");
const wr = readJson("test/fixtures/espn/projections-wr.json");
// The same Week 11, before Thursday's kickoff (IND @ HOU 2026-11-20T01:15Z).
const scoreboard: Scoreboard = parseScoreboard(readJson("test/fixtures/espn/scoreboard-upcoming.json"));
const week = scoreboard.week;
const GROUP = { qbTe: PROJECTION_GROUPS[0]!, rbKDst: PROJECTION_GROUPS[1]!, wr: PROJECTION_GROUPS[2]! };

type RawPlayer = { id: number; player: { fullName: string; defaultPositionId: number; stats: { statSourceId: number; stats: Record<string, number>; appliedTotal: number }[] } };
const raw = (json: { players: RawPlayer[] }, id: number) => {
  const p = json.players.find((x) => x.id === id)!;
  return p.player.stats.find((s) => s.statSourceId === 1)!;
};
const byId = (ps: EspnProjection[], id: string) => ps.find((p) => p.espnId === id)!;

describe("projectionsRequest", () => {
  it("asks for the week's projected stat lines of one group's ESPN slots, best projected first", () => {
    const { url, filter } = projectionsRequest(week, GROUP.qbTe);
    expect(url).toBe(`${ESPN_PROJECTIONS_HOST}/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info&scoringPeriodId=11`);
    const f = JSON.parse(filter).players;
    expect(f.filterSlotIds.value).toEqual([0, 6]);
    expect(f.filterStatsForTopScoringPeriodIds).toEqual({ value: 1, additionalValue: ["11202611"] });
    expect(f.sortAppliedStatTotal).toEqual({ sortAsc: false, sortPriority: 1, value: "11202611" });
    expect(f.limit).toBe(GROUP.qbTe.limit);
  });

  it("splits the six positions over three groups, each one request in its own run", () => {
    expect(PROJECTION_GROUPS.map((g) => g.positions)).toEqual([["QB", "TE"], ["RB", "K", "D/ST"], ["WR"]]);
  });

  it("picks the group from the run's minute (the cron fires at 9, 29 and 49)", () => {
    const at = (m: number) => groupAt(new Date(Date.UTC(2026, 10, 22, 15, m, 3)));
    expect([at(9), at(29), at(49)]).toEqual(PROJECTION_GROUPS);
    expect(at(10)).toBe(PROJECTION_GROUPS[0]); // a late run keeps its slot
  });
});

describe("espnStatMeans", () => {
  it("maps a QB's projected line to ffp.scoring's offensive stats, rounded to 3 decimals", () => {
    const s = raw(qbTe, 3918298).stats; // Josh Allen
    expect(espnStatMeans("QB", s)).toEqual({
      passing_yards: 240.867,
      passing_tds: 1.72,
      passing_interceptions: 0.653,
      passing_2pt: 0.138,
      rushing_yards: 32.528,
      rushing_tds: 0.835,
      rushing_2pt: 0.032,
      fumbles_lost: 0.222,
    });
  });

  it("maps a kicker's FG bands onto Sleeper's buckets: under 40 → fgm_30_39, 40–49 → fgm_40_49, 50+ → fgm_50_59 (less 60+)", () => {
    const s = raw(rbKDst, 3953687).stats; // Brandon Aubrey
    expect(espnStatMeans("K", s)).toEqual({ fgm_30_39: 1.226, fgm_40_49: 0.574, fgm_50_59: 0.435, fg_missed: 0.312, pat_made: 3.378, pat_missed: 0.069 });
    expect(espnStatMeans("K", { "74": 0.5, "201": 0.1 })).toEqual({ fgm_50_59: 0.4, fgm_60p: 0.1 });
  });

  it("every ESPN FG band scores the same across its Sleeper buckets under every Scoring Rules option", () => {
    // Under 40 → fgm_30_39 is exact only while 0–39 is one tier; 50+ less 60+ → fgm_50_59 only while 50–59 is.
    for (const name of RULES_NAMES) {
      const tiers = RULES[name]!.kicker.fg_made_by_distance ?? [];
      for (const [lo, hi] of [[0, 39], [40, 49], [50, 59]]) {
        for (let y = lo!; y <= hi!; y++) expect(tierPoints(y, tiers), `${name} ${y}`).toBe(tierPoints(lo!, tiers));
      }
    }
  });

  it("maps a D/ST's projected counts, Points Allowed and Yards Allowed", () => {
    const s = raw(rbKDst, -16023).stats; // Steelers D/ST
    expect(espnStatMeans("D/ST", s)).toEqual({
      sacks: 3.254,
      interceptions: 1.079,
      fumble_recoveries: 0.656,
      forced_fumbles: 1.009,
      safeties: 0.015,
      blocked_kicks: 0.113,
      interception_return_tds: 0.062,
      fumble_return_tds: 0.03,
      kickoff_return_tds: 0.006,
      punt_return_tds: 0.026,
      blocked_kick_return_tds: 0.007,
      points_allowed_all: 12.536,
      yards_allowed: 286.22,
    });
  });

  it("leaves out zeros and negatives (the schema's statMeans are ≥ 0)", () => {
    expect(espnStatMeans("WR", { "53": 0, "42": -0.001, "43": 0.0004 })).toEqual({});
  });

  it("throws on a stat that isn't a number", () => {
    expect(() => espnStatMeans("WR", { "53": "5" as unknown as number })).toThrow(EspnProjectionsError);
  });
});

describe("espnExpected", () => {
  const allPlayers = [qbTe, rbKDst, wr].flatMap((j) => (j.players as RawPlayer[]).map((p) => ({ json: j, p })));

  it("matches ESPN's own PPR points (leaguedefaults/3) for every recorded player, less the return TDs ffp doesn't score on offense", () => {
    const POS: Record<number, "QB" | "RB" | "WR" | "TE" | "K" | "D/ST"> = { 1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST" };
    for (const { p } of allPlayers) {
      const line = p.player.stats.find((s) => s.statSourceId === 1)!;
      const position = POS[p.player.defaultPositionId]!;
      const s = line.stats;
      const returns = position === "K" || position === "D/ST" ? 0 : 6 * ((s["101"] ?? 0) + (s["102"] ?? 0) + (s["63"] ?? 0));
      const got = espnExpected(position, espnStatMeans(position, s), s).ppr!;
      // statMeans are rounded to 3 decimals, so allow a few hundredths.
      expect(got, p.player.fullName).toBeCloseTo(line.appliedTotal - returns, 1);
    }
  });

  it("scores every Scoring Rules option, to 2 decimals", () => {
    const s = raw(wr, 4362628).stats; // Ja'Marr Chase: 8.725 receptions
    const e = espnExpected("WR", espnStatMeans("WR", s), s);
    expect(Object.keys(e)).toEqual(RULES_NAMES);
    expect(e.ppr! - e.standard!).toBeCloseTo(8.725, 1);
    expect(e["half-ppr"]! - e.standard!).toBeCloseTo(4.3625, 1);
    for (const v of Object.values(e)) expect(v).toBe(Math.round(v * 100) / 100);
  });

  it("scores a D/ST's Points and Yards Allowed tiers from ESPN's tier probabilities, not the tier of the mean", () => {
    const s = raw(rbKDst, -16023).stats;
    const means = espnStatMeans("D/ST", s);
    const e = espnExpected("D/ST", means, s);
    // Mean PA 12.5 is in the 7–13 tier (3 points) and mean YA 286 in 200–299 (2 points), but
    // ESPN's probabilities spread over every tier: 0.91 and −0.37 points expected.
    expect(e.ppr).toBeCloseTo(8.3, 1);
    const ofMean = espnExpected("D/ST", means, {});
    expect(ofMean.ppr! - e.ppr!).toBeCloseTo(3 - 0.907 + 2 - -0.365, 1);
  });
});

describe("parseProjections", () => {
  it("reads every player of the group's positions with a projection for this week", () => {
    const ps = parseProjections(rbKDst, week, GROUP.rbKDst.positions);
    const gibbs = byId(ps, "4429795");
    expect(gibbs).toMatchObject({ espnId: "4429795", name: "Jahmyr Gibbs", position: "RB", team: "DET" });
    expect(gibbs.expected.ppr).toBeCloseTo(21.94, 1);
    expect(byId(ps, "-16023")).toMatchObject({ name: "Steelers D/ST", position: "D/ST", team: "PIT" });
    expect(byId(ps, "3953687")).toMatchObject({ name: "Brandon Aubrey", position: "K", team: "DAL" });
  });

  it("uses only the projected (statSourceId 1) week line, not last week's actuals", () => {
    const ps = parseProjections(qbTe, week, GROUP.qbTe.positions);
    expect(byId(ps, "3918298").statMeans.passing_yards).toBe(240.867);
    // Next week's projections aren't in this response: nobody has a line.
    expect(parseProjections(qbTe, { ...week, number: 12 }, GROUP.qbTe.positions)).toEqual([]);
  });

  it("leaves out players projected for nothing (Out, injured reserve, byes)", () => {
    const ids = parseProjections(qbTe, week, GROUP.qbTe.positions).map((p) => p.espnId);
    expect(ids).not.toContain("3917315"); // Kyler Murray, Out, 0
    expect(ids).not.toContain("4431346"); // Mark Redman, GB on bye, 0
  });

  it("leaves out positions outside the group, and free agents", () => {
    expect(parseProjections(qbTe, week, ["QB"]).every((p) => p.position === "QB")).toBe(true);
    const fa = structuredClone(wr);
    fa.players[0].player.proTeamId = 0;
    expect(parseProjections(fa, week, ["WR"]).map((p) => p.espnId)).not.toContain("4362628");
  });

  it("maps ESPN's team ids to Sleeper's codes (WSH → WAS)", () => {
    const w = structuredClone(wr);
    w.players[0].player.proTeamId = 28;
    expect(byId(parseProjections(w, week, ["WR"]), "4362628").team).toBe("WAS");
  });

  it.each([
    ["not an object", [], /response/],
    ["no players list", { players: "x" }, /players/],
    ["a player without an id", { players: [{ player: { defaultPositionId: 1, proTeamId: 2, stats: [] } }] }, /id/],
    ["a player without a player object", { players: [{ id: 1 }] }, /player/],
    ["an unknown team id", { players: [{ id: 1, player: { id: 1, fullName: "X", defaultPositionId: 1, proTeamId: 99, stats: [] } }] }, /team id 99/],
    ["stats that aren't a list", { players: [{ id: 1, player: { id: 1, fullName: "X", defaultPositionId: 1, proTeamId: 2, stats: {} } }] }, /stats/],
  ])("throws an EspnProjectionsError on %s", (_what, json, message) => {
    expect(() => parseProjections(json, week, ["QB"])).toThrow(EspnProjectionsError);
    expect(() => parseProjections(json, week, ["QB"])).toThrow(message);
  });
});

describe("buildEspnStore", () => {
  const all = parseProjections(rbKDst, week, GROUP.rbKDst.positions);
  const idmap: IdMapStore = {
    schemaVersion: 1,
    generatedAt: "2026-11-19T20:00:00Z",
    espnToSleeper: { "4429795": "9221", "3953687": "7839", "4242335": "6813", "4035538": "4199" }, // Gibbs, Aubrey, Taylor, Montgomery
  };
  const before = new Date("2026-11-19T21:09:00Z"); // before Thursday's kickoff
  const build = (o: Partial<Parameters<typeof buildEspnStore>[0]> = {}) =>
    buildEspnStore({ stored: null, scoreboard, projections: all, idmap, positions: GROUP.rbKDst.positions, now: before, ...o });

  it("keys players by Sleeper id through idmap and D/STs by team; counts the unmapped", () => {
    const { store, counts } = build();
    expect(store).toMatchObject({ schemaVersion: 1, season: 2026, seasonType: "regular", week: 11, fetchedAt: "2026-11-19T21:09:00Z" });
    expect(Object.keys(store.players)).toEqual(
      ["dst:BUF", "dst:HOU", "dst:IND", "dst:JAX", "dst:KC", "dst:PIT", "sleeper:4199", "sleeper:6813", "sleeper:7839", "sleeper:9221"],
    );
    expect(counts).toEqual({ written: 10, unmapped: all.length - 6 - 4, noGame: 0 });
  });

  it("takes team and kickoff from the scoreboard's Game", () => {
    const { store } = build();
    expect(store.players["sleeper:9221"]).toMatchObject({ name: "Jahmyr Gibbs", team: "DET", position: "RB", kickoff: "2026-11-22T18:00:00Z" });
    expect(store.players["dst:HOU"]).toMatchObject({ name: "Texans D/ST", team: "HOU", kickoff: "2026-11-20T01:15:00Z" });
    const e = store.players["sleeper:7839"]!;
    expect(e.statMeans).toEqual(byId(all, "3953687").statMeans);
    expect(e.expected).toEqual(byId(all, "3953687").expected);
  });

  it("leaves out teams that don't play this week", () => {
    const noDal = { ...scoreboard, games: scoreboard.games.filter((g) => g.home !== "DAL" && g.away !== "DAL") };
    const { store, counts } = build({ scoreboard: noDal });
    expect(store.players["sleeper:7839"]).toBeUndefined(); // Aubrey, DAL
    expect(counts.noGame).toBe(1);
  });

  it("still writes the D/STs when idmap is missing", () => {
    const { store, counts } = build({ idmap: null });
    expect(Object.keys(store.players).every((k) => k.startsWith("dst:"))).toBe(true);
    expect(Object.keys(store.players)).toHaveLength(6);
    expect(counts.unmapped).toBe(all.length - 6);
  });

  it("keeps an entry frozen once its Game has kicked off, and refreshes the rest", () => {
    const first = build().store;
    const changed = all.map((p) => ({ ...p, expected: { ...p.expected, ppr: p.expected.ppr! + 1 } }));
    const after = new Date("2026-11-20T02:09:00Z"); // Thursday's Game is Live
    const { store } = build({ stored: first, projections: changed, now: after });
    expect(store.players["dst:HOU"]).toEqual(first.players["dst:HOU"]);
    expect(store.players["sleeper:6813"]).toEqual(first.players["sleeper:6813"]); // Taylor, IND
    expect(store.players["sleeper:9221"]!.expected.ppr).toBeCloseTo(first.players["sleeper:9221"]!.expected.ppr! + 1, 6);
    expect(store.fetchedAt).toBe(first.fetchedAt); // the run sets it when it writes
  });

  it("takes a player whose Game has kicked off when nothing is stored for him", () => {
    const after = new Date("2026-11-20T02:09:00Z");
    expect(build({ now: after }).store.players["dst:HOU"]).toBeDefined();
  });

  it("drops a stored player of this group who is no longer projected before his kickoff; other groups' players stay", () => {
    const first = build().store;
    const qb: EspnStore = { ...first, players: { ...first.players, "sleeper:4984": { ...first.players["sleeper:9221"]!, name: "Josh Allen", position: "QB" } } };
    const { store } = build({ stored: qb, projections: all.filter((p) => p.espnId !== "4429795") });
    expect(store.players["sleeper:9221"]).toBeUndefined();
    expect(store.players["sleeper:4984"]).toBeDefined();
  });

  it("serializes the same as the stored store when the players are the same, fetchedAt included", () => {
    const first = build().store;
    const stored = JSON.parse(JSON.stringify(first));
    const later = build({ stored, now: new Date("2026-11-19T22:09:00Z") });
    expect(JSON.stringify(later.store)).toBe(JSON.stringify(first));
  });

  it("replaces a store for another week", () => {
    const first = build().store;
    const old: EspnStore = { ...first, week: 10, players: { "dst:PIT": first.players["dst:PIT"]!, "sleeper:1": { ...first.players["dst:PIT"]!, kickoff: "2026-11-16T18:00:00Z" } } };
    const { store } = build({ stored: old });
    expect(store.week).toBe(11);
    expect(store.fetchedAt).toBe("2026-11-19T21:09:00Z");
    expect(store.players["sleeper:1"]).toBeUndefined();
  });
});
