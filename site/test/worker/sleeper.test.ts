import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { RULES, RULES_NAMES, score, type ScoringRules } from "../../src/worker/scoring.js";
import { SLEEPER_OFFENSE_KEYS, SleeperShapeError, sleeperStatsToPlayers, type SleeperPlayer } from "../../src/worker/sleeper.js";

const read = (path: string) => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
// One Game, live in Q3 (S9.1), straight from Sleeper.
const live = read("../fixtures/sleeper/stats-live.json");
// A completed week (2026 Week 10), trimmed to rows with 2-pt conversions, lost fumbles, interceptions,
// a FB, a DB with WR eligibility, gms_active-only rows, a K and a DEF.
const week10 = read("./fixtures/sleeper-week10-final.json");

const ppr = RULES.ppr!;
// Sleeper's own `pts_ppr` takes 1 point per interception; the Leaderboard's ppr takes 2 (ESPN's default).
// Everything else on an offensive line matches, so this is the rule Sleeper's number is checked against.
const sleeperPpr: ScoringRules = { ...ppr, offense: { ...ppr.offense, passing_interceptions: -1 } };

const byId = (players: SleeperPlayer[], id: string) => {
  const p = players.find((x) => x.id === id);
  if (!p) throw new Error(`no ${id}`);
  return p;
};
const row = (over: Record<string, unknown> = {}) => ({
  player_id: "1",
  team: "KC",
  game_id: "202611113",
  stats: {},
  player: { first_name: "Pat", last_name: "Doe", position: "QB", fantasy_positions: ["QB"] },
  ...over,
});

describe("sleeperStatsToPlayers: one row", () => {
  it("keys the player by Sleeper id, with name, team, position and Sleeper's game id", () => {
    const { players } = sleeperStatsToPlayers([row({ player_id: "9758", team: "HOU" })]);
    expect(players).toEqual([
      { kind: "offense", id: "sleeper:9758", name: "Pat Doe", team: "HOU", position: "QB", sleeperGameId: "202611113", stats: {} },
    ]);
  });

  it("maps every Sleeper key to its ffp.scoring Scoring Stat and keeps nothing else", () => {
    const stats: Record<string, number> = { gp: 1, pts_ppr: 99, rec_tgt: 9, rush_att: 4, bonus_rec_te: 3 };
    const expected: Record<string, number> = {};
    Object.entries(SLEEPER_OFFENSE_KEYS).forEach(([sleeperKey, stat], i) => {
      stats[sleeperKey] = i + 1;
      expected[stat] = i + 1;
    });
    expect(sleeperStatsToPlayers([row({ stats })]).players[0]!.stats).toEqual(expected);
  });

  it("covers every offensive Scoring Stat with Sleeper's key names", () => {
    expect(SLEEPER_OFFENSE_KEYS).toEqual({
      pass_yd: "passing_yards",
      pass_td: "passing_tds",
      pass_int: "passing_interceptions",
      pass_2pt: "passing_2pt",
      rush_yd: "rushing_yards",
      rush_td: "rushing_tds",
      rush_2pt: "rushing_2pt",
      rec: "receptions",
      rec_yd: "receiving_yards",
      rec_td: "receiving_tds",
      rec_2pt: "receiving_2pt",
      fum_lost: "fumbles_lost",
    });
  });

  it("keeps a player with no stats, or only gms_active-style keys, with an empty stat line", () => {
    const rows = [
      row({ player_id: "1", stats: { gms_active: 1, pos_rank_ppr: 999 } }),
      row({ player_id: "2", stats: {} }),
      row({ player_id: "3", stats: null }),
      row({ player_id: "4", stats: undefined }),
    ];
    const { players } = sleeperStatsToPlayers(rows);
    expect(players.map((p) => [p.id, p.stats])).toEqual([
      ["sleeper:1", {}],
      ["sleeper:2", {}],
      ["sleeper:3", {}],
      ["sleeper:4", {}],
    ]);
    for (const p of players) expect(score(p.stats, ppr)).toBe(0);
  });

  it("keeps negative yards and leaves out non-numbers", () => {
    const { players } = sleeperStatsToPlayers([row({ stats: { rec_yd: -1, rush_yd: 0, pass_yd: null, rec: "2" } })]);
    expect(players[0]!.stats).toEqual({ receiving_yards: -1, rushing_yards: 0 });
  });

  it("lists a FB as RB, and a non-offense player at his first offense fantasy position", () => {
    const { players } = sleeperStatsToPlayers([
      row({ player_id: "1", player: { first_name: "A", last_name: "B", position: "FB", fantasy_positions: ["RB"] } }),
      row({ player_id: "2", player: { first_name: "C", last_name: "D", position: "DB", fantasy_positions: ["DB", "WR"] } }),
    ]);
    expect(players.map((p) => p.position)).toEqual(["RB", "WR"]);
  });

  it("skips K and DEF rows (Sprint 11) and players with no offense position, counting each", () => {
    const { players, skipped } = sleeperStatsToPlayers([
      row({ player_id: "1", player: { first_name: "K", last_name: "K", position: "K", fantasy_positions: ["K"] } }),
      row({ player_id: "HOU", player: { first_name: "Houston", last_name: "Texans", position: "DEF", fantasy_positions: ["DEF"] } }),
      row({ player_id: "3", player: { first_name: "L", last_name: "B", position: "LB", fantasy_positions: ["LB"] } }),
      row({ player_id: "4" }),
    ]);
    expect(players.map((p) => p.id)).toEqual(["sleeper:4"]);
    expect(skipped).toEqual({ K: 1, DEF: 1, other: 1 });
  });

  it("gives a null Sleeper game id when the row has none", () => {
    expect(sleeperStatsToPlayers([row({ game_id: undefined })]).players[0]!.sleeperGameId).toBeNull();
  });

  it("rejects a response that isn't a list of player rows", () => {
    expect(() => sleeperStatsToPlayers({} as unknown as unknown[])).toThrow(SleeperShapeError);
    expect(() => sleeperStatsToPlayers([row({ player: null })])).toThrow(SleeperShapeError);
    expect(() => sleeperStatsToPlayers([row({ player_id: 12 })])).toThrow(SleeperShapeError);
    expect(() => sleeperStatsToPlayers([row({ team: null })])).toThrow(SleeperShapeError);
  });
});

describe("sleeperStatsToPlayers: the live fixture end to end (Sleeper → stat lines → ppr points)", () => {
  const { players, skipped } = sleeperStatsToPlayers(live);

  it("lists every QB/RB/WR/TE row once and skips the Ks and DEFs", () => {
    expect(live).toHaveLength(50);
    expect(players).toHaveLength(46);
    expect(skipped).toEqual({ K: 2, DEF: 2, other: 0 });
    expect(new Set(players.map((p) => p.id)).size).toBe(46);
    for (const p of players) {
      expect(p.id).toMatch(/^sleeper:\d+$/); // leaderboard.schema.json's player id
      expect(["HOU", "IND"]).toContain(p.team);
      expect(p.sleeperGameId).toBe("202611113"); // Sleeper's own id, not ESPN's event id (401873066)
    }
  });

  it("scores known players under ppr", () => {
    const pts = (id: string) => score(byId(players, id).stats, ppr);
    // C.J. Stroud: 134 pass yd, 2 pass TD, 1 INT, 4 rush yd = 5.36 + 8 - 2 + 0.4.
    expect(pts("sleeper:9758")).toBeCloseTo(11.76, 6);
    // Nico Collins: 2 rec, 48 yd, 1 TD.
    expect(pts("sleeper:7569")).toBeCloseTo(12.8, 6);
    // Jonathan Taylor: 17 rush yd, 1 rec for -1 yd.
    expect(pts("sleeper:6813")).toBeCloseTo(2.6, 6);
    expect(byId(players, "sleeper:9758")).toMatchObject({ name: "C.J. Stroud", team: "HOU", position: "QB" });
  });

  it("totals the whole Game under every Scoring Rules option", () => {
    const totals = Object.fromEntries(RULES_NAMES.map((n) => [n, players.reduce((sum, p) => sum + score(p.stats, RULES[n]!), 0)]));
    // Receptions are the only offense difference between ppr, half-ppr and standard.
    const receptions = players.reduce((sum, p) => sum + (p.stats.receptions ?? 0), 0);
    expect(totals.ppr! - totals["half-ppr"]!).toBeCloseTo(receptions / 2, 6);
    expect(totals.ppr! - totals.standard!).toBeCloseTo(receptions, 6);
    // espn-league is ppr on offense (full PPR, 4-pt pass TD).
    expect(totals["espn-league"]).toBeCloseTo(totals.ppr!, 6);
  });
});

describe("Sleeper's own pts_ppr agrees with our scorer", () => {
  // Sleeper's pts_ppr uses a -1 interception; ours uses -2. Under Sleeper's rule every offensive
  // player who has a pts_ppr matches it to Sleeper's 2-decimal rounding, and under ours he scores
  // exactly 1 point less per interception.
  for (const [label, rows] of [
    ["live fixture", live],
    ["completed Week 10", week10],
  ] as const) {
    it(`for every scored player in the ${label}`, () => {
      const { players } = sleeperStatsToPlayers(rows);
      let checked = 0;
      for (const p of players) {
        const raw = rows.find((r: { player_id: string }) => `sleeper:${r.player_id}` === p.id).stats;
        if (raw.pts_ppr === undefined) continue;
        checked++;
        expect(score(p.stats, sleeperPpr), p.name).toBeCloseTo(raw.pts_ppr, 2);
        expect(score(p.stats, ppr) - raw.pts_ppr, p.name).toBeCloseTo(-(raw.pass_int ?? 0), 2);
      }
      expect(checked).toBeGreaterThan(10);
    });
  }

  it("including 2-pt conversions and lost fumbles (Week 10)", () => {
    const { players } = sleeperStatsToPlayers(week10);
    // Christian McCaffrey: a receiving 2-pt; Dak Prescott: a rushing 2-pt and an INT; Drew Lock: 2 lost fumbles.
    expect(byId(players, "sleeper:4034").stats.receiving_2pt).toBe(1);
    expect(byId(players, "sleeper:3294").stats).toMatchObject({ rushing_2pt: 1, passing_interceptions: 1 });
    expect(byId(players, "sleeper:5854").stats.fumbles_lost).toBe(2);
    expect(score(byId(players, "sleeper:4034").stats, ppr)).toBeCloseTo(46.3, 6);
    expect(score(byId(players, "sleeper:3294").stats, ppr)).toBeCloseTo(35.42 - 1, 6);
    expect(score(byId(players, "sleeper:5854").stats, ppr)).toBeCloseTo(15.6, 6);
  });

  it("keeps Week 10's gms_active-only players with 0 points", () => {
    const { players } = sleeperStatsToPlayers(week10);
    for (const id of ["sleeper:14071", "sleeper:11655"]) {
      expect(byId(players, id).stats).toEqual({});
    }
    expect(byId(players, "sleeper:14071").position).toBe("RB"); // a FB
    expect(byId(players, "sleeper:12530").position).toBe("WR"); // Travis Hunter, a DB eligible at WR
  });
});
