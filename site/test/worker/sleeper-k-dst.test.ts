import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { RULES, RULES_NAMES, scoreDst, scoreKicker, scoreLine, type DstStatLine, type ScoringRules } from "../../src/worker/scoring.js";
import {
  SLEEPER_DST_KEYS,
  SLEEPER_KICKER_KEYS,
  sleeperStatsToPlayers,
  SleeperShapeError,
  type SleeperDstPlayer,
  type SleeperKickerPlayer,
} from "../../src/worker/sleeper.js";

const read = (path: string) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
// Every K and DEF row of the completed 2026 Week 10 (35 K, 28 DEF), straight from Sleeper, trimmed
// to the keys this module reads plus Sleeper's own points and the keys it must ignore.
const week10: SleeperRowFixture[] = read("../fixtures/sleeper/stats-week10-k-dst.json");
// The same week's D/ST lines from nflverse play-by-play, as ffp builds them (scripts/build_params.py dst_games).
const nflverse: Record<string, Required<DstStatLine>> = read("../fixtures/sleeper/nflverse-week10-dst.json").teams;
const live = read("../fixtures/sleeper/stats-live.json");

interface SleeperRowFixture {
  player_id: string;
  team: string;
  opponent: string;
  stats: Record<string, number>;
  player: { position: string };
}

const ppr = RULES.ppr!;
// Sleeper's default K and DEF scoring, as its own pts_std on these rows applies it: the ppr kicker
// (3/4/5 by distance, no 60+ bonus) with −1 per PAT missed; ffp's `sleeper` D/ST preset.
const sleeperRules: ScoringRules = {
  ...ppr,
  kicker: { ...ppr.kicker, pat_missed: -1 },
  dst: {
    sacks: 1, interceptions: 2, fumble_recoveries: 2, forced_fumbles: 1, safeties: 2, blocked_kicks: 2,
    interception_return_tds: 6, fumble_return_tds: 6, kickoff_return_tds: 6, punt_return_tds: 6, blocked_kick_return_tds: 6,
    points_allowed_excludes: ["opponent_defensive_tds"],
    points_allowed_tiers: [
      { min: 0, max: 0, points: 10 }, { min: 1, max: 6, points: 7 }, { min: 7, max: 13, points: 4 }, { min: 14, max: 20, points: 1 },
      { min: 21, max: 27, points: 0 }, { min: 28, max: 34, points: -1 }, { min: 35, max: null, points: -4 },
    ],
  },
};

const row = (over: Record<string, unknown> = {}) => ({
  player_id: "4227",
  team: "BAL",
  opponent: "LAC",
  game_id: "202611010",
  stats: {},
  player: { first_name: "Tyler", last_name: "Loop", position: "K", fantasy_positions: ["K"] },
  ...over,
});
const defRow = (team: string, opponent: string, stats: Record<string, unknown>, last = "Ravens") =>
  row({ player_id: team, team, opponent, stats, player: { first_name: "Baltimore", last_name: last, position: "DEF", fantasy_positions: ["DEF"] } });

const kickers = (rows: readonly unknown[]) => sleeperStatsToPlayers(rows).players.filter((p): p is SleeperKickerPlayer => p.kind === "kicker");
const dsts = (rows: readonly unknown[]) => sleeperStatsToPlayers(rows).players.filter((p): p is SleeperDstPlayer => p.kind === "dst");

describe("sleeperStatsToPlayers: a K row", () => {
  it("becomes a kicker keyed by Sleeper id, with FGs made by bucket, PATs and misses", () => {
    const stats = {
      fgm_0_19: 1, fgm_20_29: 1, fgm_30_39: 2, fgm_40_49: 1, fgm_50_59: 1, fgm_60p: 1, fgmiss: 2, xpm: 3, xpmiss: 1,
      // Totals, and the overlapping 50+ bucket: not read.
      fgm: 7, fgm_yds: 260, fgm_lng: 61, fgm_50p: 2, fga: 9, xpa: 4, fgmiss_50p: 1, pts_std: 99, st_ff: 1,
    };
    expect(kickers([row({ stats })])).toEqual([
      {
        kind: "kicker",
        id: "sleeper:4227",
        name: "Tyler Loop",
        team: "BAL",
        position: "K",
        sleeperGameId: "202611010",
        stats: { fgm_0_19: 1, fgm_20_29: 1, fgm_30_39: 2, fgm_40_49: 1, fgm_50_59: 1, fgm_60p: 1, fg_missed: 2, pat_made: 3, pat_missed: 1 },
      },
    ]);
  });

  it("covers every FG bucket and kicker Scoring Stat with Sleeper's key names", () => {
    expect(SLEEPER_KICKER_KEYS).toEqual({
      fgm_0_19: "fgm_0_19",
      fgm_20_29: "fgm_20_29",
      fgm_30_39: "fgm_30_39",
      fgm_40_49: "fgm_40_49",
      fgm_50_59: "fgm_50_59",
      fgm_60p: "fgm_60p",
      xpm: "pat_made",
      xpmiss: "pat_missed",
      fgmiss: "fg_missed",
    });
  });

  it("keeps a kicker with no kicks yet, with an empty stat line and 0 points", () => {
    const [k] = kickers([row({ stats: { gms_active: 1, pos_rank_ppr: 999 } }), row({ player_id: "2", stats: null })]);
    expect(k!.stats).toEqual({});
    expect(kickers([row({ stats: null })])[0]!.stats).toEqual({});
    for (const name of RULES_NAMES) expect(scoreLine(k!, RULES[name]!)).toBe(0);
  });

  it("rejects a K row with no player_id or team", () => {
    expect(() => sleeperStatsToPlayers([row({ player_id: undefined })])).toThrow(SleeperShapeError);
    expect(() => sleeperStatsToPlayers([row({ team: "" })])).toThrow(SleeperShapeError);
  });
});

describe("sleeperStatsToPlayers: a DEF row", () => {
  it("becomes a D/ST keyed by team, named for the nickname", () => {
    const [d] = dsts([defRow("BAL", "LAC", { sack: 2 })]);
    expect(d).toEqual({
      kind: "dst",
      id: "dst:BAL",
      name: "Ravens D/ST",
      team: "BAL",
      position: "D/ST",
      sleeperGameId: "202611010",
      stats: { sacks: 2, points_allowed_all: 0 },
    });
  });

  it("maps Sleeper's DEF keys to ffp's D/ST stats, counting special-teams fumbles with the rest", () => {
    const stats = {
      sack: 3, int: 2, fum_rec: 1, def_st_fum_rec: 1, ff: 2, def_st_ff: 1, safe: 1, blk_kick: 1, def_td: 1, def_st_td: 1,
      pts_allow: 17, yds_allow: 288,
      // Not read: buckets (we tier pts_allow ourselves), Sleeper's points, unscored extras.
      pts_allow_14_20: 1, yds_allow_200_299: 1, pts_std: 30, td: 2, misc_td: 1, def_2pt: 1, fum_rec_ez_tds: 1, sack_yd: 20,
    };
    expect(dsts([defRow("BAL", "LAC", stats)])[0]!.stats).toEqual({
      sacks: 3,
      interceptions: 2,
      fumble_recoveries: 2,
      forced_fumbles: 3,
      safeties: 1,
      blocked_kicks: 1,
      // Sleeper doesn't split INT from fumble return TDs, or kick from punt and blocked-kick returns.
      interception_return_tds: 1,
      kickoff_return_tds: 1,
      points_allowed_all: 17,
      yards_allowed: 288,
    });
  });

  it("documents which Sleeper key feeds which D/ST stat", () => {
    expect(SLEEPER_DST_KEYS).toEqual({
      sack: "sacks",
      int: "interceptions",
      fum_rec: "fumble_recoveries",
      def_st_fum_rec: "fumble_recoveries",
      ff: "forced_fumbles",
      def_st_ff: "forced_fumbles",
      safe: "safeties",
      blk_kick: "blocked_kicks",
      def_td: "interception_return_tds",
      def_st_td: "kickoff_return_tds",
      yds_allow: "yards_allowed",
    });
  });

  it("adds back 6 per opponent defensive TD, which Sleeper's pts_allow leaves out, and records the opponent's TD points", () => {
    const rows = [
      defRow("BAL", "LAC", { pts_allow: 17 }),
      defRow("LAC", "BAL", { pts_allow: 20, def_td: 2, def_st_td: 1 }, "Chargers"),
    ];
    const bal = dsts(rows).find((d) => d.team === "BAL")!;
    expect(bal.stats).toEqual({ points_allowed_all: 29, opponent_defensive_tds_points: 12, opponent_return_tds_points: 6 });
    // Every Leaderboard option leaves the opponent's defensive TDs out, as Sleeper does: 17 again.
    expect(dsts(rows).find((d) => d.team === "LAC")!.stats).toMatchObject({ points_allowed_all: 20 });
  });

  it("takes the opponent's TDs whatever order the rows come in, and none when the opponent has no row yet", () => {
    const rows = [defRow("LAC", "BAL", { def_td: 1 }, "Chargers"), defRow("BAL", "LAC", { pts_allow: 7 })];
    expect(dsts(rows).find((d) => d.team === "BAL")!.stats).toEqual({ points_allowed_all: 13, opponent_defensive_tds_points: 6 });
    expect(dsts([defRow("BAL", "LAC", { pts_allow: 7 })])[0]!.stats).toEqual({ points_allowed_all: 7 });
    expect(dsts([defRow("BAL", "", { pts_allow: 7 })])[0]!.stats).toEqual({ points_allowed_all: 7 });
  });

  it("gives a D/ST with no stats yet 0 Points Allowed (the top tier, as the scorer would)", () => {
    expect(dsts([defRow("BAL", "LAC", {}), defRow("LAC", "BAL", null as unknown as Record<string, unknown>)]).map((d) => d.stats)).toEqual([
      { points_allowed_all: 0 },
      { points_allowed_all: 0 },
    ]);
  });

  it("falls back to the team as the name when Sleeper gives no nickname", () => {
    const r = row({ player_id: "BAL", stats: {}, player: { position: "DEF", fantasy_positions: ["DEF"] } });
    expect(dsts([r])[0]!.name).toBe("BAL D/ST");
  });

  it("rejects a DEF row with no team", () => {
    expect(() => sleeperStatsToPlayers([defRow("", "LAC", {})])).toThrow(SleeperShapeError);
  });

  it("is safe to leave the TD kinds unsplit: every Leaderboard option scores every return TD alike", () => {
    for (const name of RULES_NAMES) {
      const d = RULES[name]!.dst;
      expect(d.fumble_return_tds, name).toBe(d.interception_return_tds);
      for (const k of ["punt_return_tds", "blocked_kick_return_tds"] as const) expect(d[k], name).toBe(d.kickoff_return_tds);
      expect(d.points_allowed_excludes ?? [], name).not.toContain("opponent_return_tds");
    }
  });
});

describe("Week 10's K and DEF rows (2026, completed)", () => {
  const players = sleeperStatsToPlayers(week10).players;
  const raw = (id: string) => week10.find((r) => (r.player.position === "DEF" ? `dst:${r.team}` : `sleeper:${r.player_id}`) === id)!;

  it("turns every row into a player, none skipped", () => {
    const { skipped } = sleeperStatsToPlayers(week10);
    expect(players.filter((p) => p.kind === "kicker")).toHaveLength(35);
    expect(players.filter((p) => p.kind === "dst")).toHaveLength(28);
    expect(skipped).toEqual({ other: 0 });
  });

  it("matches Sleeper's own pts_std on every kicker, but for a kicker's special-teams forced fumble", () => {
    let checked = 0;
    for (const p of players) {
      if (p.kind !== "kicker") continue;
      const s = raw(p.id).stats;
      if (s.pts_std === undefined) continue;
      checked++;
      // Sleeper gives a kicker 1 point for a forced fumble on a return (st_ff); ffp has no such kicker stat.
      expect(scoreKicker(p.stats, sleeperRules), p.name).toBeCloseTo(s.pts_std - (s.st_ff ?? 0), 6);
    }
    expect(checked).toBe(28);
  });

  it("matches Sleeper's own pts_std on every D/ST, but for special-teams fumble recoveries", () => {
    let checked = 0;
    for (const p of players) {
      if (p.kind !== "dst") continue;
      const s = raw(p.id).stats;
      if (s.pts_std === undefined) continue;
      checked++;
      // Sleeper scores a special-teams recovery 1 (def_st_fum_rec); ffp counts it as a fumble recovery, 2.
      expect(scoreDst(p.stats, sleeperRules), p.name).toBeCloseTo(s.pts_std + (s.def_st_fum_rec ?? 0), 6);
    }
    expect(checked).toBe(27);
  });

  it("agrees with nflverse on every D/ST's sacks, INTs, safeties, blocks, TDs, Points and Yards Allowed", () => {
    for (const p of players) {
      if (p.kind !== "dst") continue;
      const n = nflverse[p.team]!;
      const s = p.stats;
      expect(n, p.team).toBeDefined();
      expect([s.sacks ?? 0, s.interceptions ?? 0, s.safeties ?? 0, s.blocked_kicks ?? 0], p.team).toEqual([n.sacks, n.interceptions, n.safeties, n.blocked_kicks]);
      expect(s.interception_return_tds ?? 0, p.team).toBe(n.interception_return_tds + n.fumble_return_tds);
      expect(s.kickoff_return_tds ?? 0, p.team).toBe(n.kickoff_return_tds + n.punt_return_tds + n.blocked_kick_return_tds);
      expect(s.points_allowed_all, p.team).toBe(n.points_allowed_all);
      expect(s.yards_allowed ?? 0, p.team).toBe(n.yards_allowed);
      // nflverse counts the try after the opponent's TD too; Sleeper's rows can't separate it.
      const tryPoints = n.opponent_defensive_tds_points - (s.opponent_defensive_tds_points ?? 0);
      expect(tryPoints, p.team).toBeGreaterThanOrEqual(0);
      expect(tryPoints, p.team).toBeLessThanOrEqual(2 * ((s.opponent_defensive_tds_points ?? 0) / 6));
    }
  });

  it("agrees with nflverse on forced fumbles and recoveries for all but a few credit disputes", () => {
    const off: string[] = [];
    for (const p of players) {
      if (p.kind !== "dst") continue;
      const n = nflverse[p.team]!;
      if ((p.stats.forced_fumbles ?? 0) !== n.forced_fumbles || (p.stats.fumble_recoveries ?? 0) !== n.fumble_recoveries) off.push(p.team);
    }
    // Sleeper's data provider and nflverse credit a few forced fumbles and one recovery differently.
    expect(off.sort()).toEqual(["LV", "NYJ", "SF"]);
  });

  it("scores the same points as nflverse's lines under every Leaderboard option, but for those disputes and the try", () => {
    for (const name of RULES_NAMES) {
      const rules = RULES[name]!;
      let same = 0;
      for (const p of players) {
        if (p.kind !== "dst" || ["LV", "NYJ", "SF"].includes(p.team)) continue;
        // Points Allowed with the opponent's defensive TDs left out: nflverse also leaves out the try.
        const n = { ...nflverse[p.team]!, opponent_defensive_tds_points: p.stats.opponent_defensive_tds_points ?? 0 };
        expect(scoreDst(p.stats, rules), `${name} ${p.team}`).toBeCloseTo(scoreDst(n, rules), 6);
        same++;
      }
      expect(same).toBe(25);
    }
  });

  it("scores known D/STs and kickers under ppr", () => {
    const byId = (id: string) => players.find((p) => p.id === id)!;
    expect(byId("dst:WAS").name).toBe("Commanders D/ST");
    // Commanders: 1 sack, 1 INT, 1 fumble recovery, 21 allowed (0), 399 yards (−1).
    expect(scoreLine(byId("dst:WAS"), ppr)).toBe(4);
    // Colts: 3 sacks, 1 recovery, a fumble return TD; 16 allowed after Miami's pick-six (1); 293 yards (2).
    expect(scoreLine(byId("dst:IND"), ppr)).toBe(3 + 2 + 6 + 1 + 2);
    // Ka'imi Fairbairn: three FGs of 50–59 yards, 2 PATs.
    expect(scoreLine(byId("sleeper:6528"), ppr)).toBe(17);
    expect(scoreLine(byId("sleeper:6528"), RULES["espn-league"]!)).toBe(17);
  });
});

describe("the live fixture's Ks and DEFs (Thursday, Q3)", () => {
  it("lists both teams' kicker and D/ST", () => {
    const { players, skipped } = sleeperStatsToPlayers(live);
    expect(players.filter((p) => p.kind !== "offense").map((p) => p.id).sort()).toEqual(["dst:HOU", "dst:IND", "sleeper:12185", "sleeper:3451"]);
    expect(skipped).toEqual({ other: 0 });
    for (const p of players) {
      if (p.kind === "offense") continue;
      const s = (live as SleeperRowFixture[]).find((r) => (p.kind === "dst" ? `dst:${r.player_id}` : `sleeper:${r.player_id}`) === p.id)!.stats;
      const pts = p.kind === "kicker" ? scoreKicker(p.stats, sleeperRules) : scoreDst(p.stats, sleeperRules);
      expect(pts, p.name).toBeCloseTo(s.pts_std!, 6);
    }
  });
});
