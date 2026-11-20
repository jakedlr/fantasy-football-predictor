/**
 * A generated full Sunday for the stats run's CPU benchmark (test/worker/cpu.bench.test.ts):
 * 16 Games mid-slate (5 Final, 6 Live at different points, 5 Upcoming), Sleeper's response scaled
 * from the recorded Week 10 rows (each clone keeps a real row's full set of stats keys), and KV
 * `expected` and `espn` with about 1,000 entries each. Deterministic.
 */
import type { EspnStore, Expectation, ExpectedStore } from "../../src/worker/kv.js";
import type { Game, Scoreboard } from "../../src/worker/scoreboard.js";
import type { SleeperPlayer } from "../../src/worker/sleeper.js";
import { readJson } from "./fakes.js";

const TEAMS = [
  "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
  "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
];
const LIVE_AT: [number, string][] = [[1, "11:20"], [2, "0:00"], [2, "4:05"], [3, "9:41"], [4, "1:12"], [5, "6:00"]];

export interface FullSunday {
  scoreboard: Scoreboard;
  /** Sleeper's response body, as fetch would hand it to `res.json()`. */
  sleeperText: string;
  expectedText: string;
  espnText: string;
  /** Hand-made kickers and D/STs, until S11.1's rows replace them (sleeperStatsToPlayers skips K and DEF today). */
  kAndDst: SleeperPlayer[];
}

export function fullSunday(rows = 2000, expectations = 1000): FullSunday {
  const games: Game[] = [];
  for (let i = 0; i < 16; i++) {
    const status = i < 5 ? "final" : i < 11 ? "live" : "upcoming";
    const at = status === "live" ? LIVE_AT[i - 5]! : null;
    games.push({
      id: String(401_900_000 + i), kickoff: "2026-11-22T18:00:00Z", status,
      home: TEAMS[2 * i]!, away: TEAMS[2 * i + 1]!,
      homeScore: status === "upcoming" ? null : 17, awayScore: status === "upcoming" ? null : 13,
      period: at?.[0] ?? null, clock: at?.[1] ?? null,
    });
  }
  const scoreboard: Scoreboard = {
    week: { season: 2026, seasonType: "regular", number: 12, start: "2026-11-25T08:00:00Z", end: "2026-12-02T07:59:00Z", firstKickoff: "2026-11-22T18:00:00Z", lastKickoff: "2026-11-23T01:20:00Z" },
    games,
  };

  const recorded = readJson("test/fixtures/sleeper/stats-week10-final.json") as { player_id: string; team: string; stats: Record<string, number>; player: { position: string } }[];
  const offense = recorded.filter((r) => ["QB", "RB", "WR", "TE"].includes(r.player.position));
  const out: unknown[] = [];
  for (let i = 0; i < rows; i++) {
    const base = offense[i % offense.length]!;
    const team = TEAMS[i % TEAMS.length]!;
    // About a third of Sleeper's rows are active players with no Scoring Stat (as in stats-live.json).
    const stats = i % 3 === 2 ? { gms_active: 1, pos_rank_ppr: 999, pos_rank_half_ppr: 999, pos_rank_std: 999 } : base.stats;
    out.push({ ...base, player_id: String(10_000 + i), team, stats, player: { ...base.player, team } });
  }

  const players: Record<string, Expectation> = {};
  for (let i = 0; i < expectations; i++) {
    const team = TEAMS[i % TEAMS.length]!;
    players[`sleeper:${10_000 + i}`] = {
      name: `Player ${i}`, team, position: "WR", kickoff: "2026-11-22T18:00:00Z",
      statMeans: { receptions: 5.4, receiving_yards: 68.2, receiving_tds: 0.41, rushing_yards: 1.3, fumbles_lost: 0.03, receiving_2pt: 0.02 },
      expected: { ppr: 16.2, "half-ppr": 13.5, standard: 10.8, "espn-league": 16.2 },
    };
  }
  const kAndDst: SleeperPlayer[] = [];
  for (const team of TEAMS) {
    players[`dst:${team}`] = {
      name: `${team} D/ST`, team, position: "D/ST", kickoff: "2026-11-22T18:00:00Z",
      statMeans: { sacks: 2.6, interceptions: 0.9, fumble_recoveries: 0.6, forced_fumbles: 0.8, safeties: 0.03, interception_return_tds: 0.08, fumble_return_tds: 0.04, points_allowed_all: 21.3, opponent_defensive_tds_points: 0.5, yards_allowed: 318.4 },
      expected: { ppr: 7.4, "half-ppr": 7.4, standard: 7.4, "espn-league": 6.9 },
    };
    players[`sleeper:${90_000 + TEAMS.indexOf(team)}`] = {
      name: `${team} Kicker`, team, position: "K", kickoff: "2026-11-22T18:00:00Z",
      statMeans: { pat_made: 2.4, pat_missed: 0.1, fg_missed: 0.3, fgm_20_29: 0.5, fgm_30_39: 0.6, fgm_40_49: 0.5, fgm_50_59: 0.2, fgm_60p: 0.01 },
      expected: { ppr: 8.1, "half-ppr": 8.1, standard: 8.1, "espn-league": 8.6 },
    };
    kAndDst.push(
      { kind: "dst", id: `dst:${team}`, name: `${team} D/ST`, team, position: "D/ST", sleeperGameId: null, stats: { sacks: 2, interceptions: 1, points_allowed_all: 13, yards_allowed: 240 } },
      { kind: "kicker", id: `sleeper:${90_000 + TEAMS.indexOf(team)}`, name: `${team} Kicker`, team, position: "K", sleeperGameId: null, stats: { pat_made: 2, fgm_40_49: 1 } },
    );
  }
  const week = { schemaVersion: 1 as const, season: 2026, seasonType: "regular" as const, week: 12 };
  const expected: ExpectedStore = { ...week, generatedAt: "2026-11-22T17:50:00Z", players };
  // ESPN projects about as many players, keyed the same way.
  const espn: EspnStore = { ...week, fetchedAt: "2026-11-22T17:40:00Z", players };
  return { scoreboard, sleeperText: JSON.stringify(out), expectedText: JSON.stringify(expected), espnText: JSON.stringify(espn), kAndDst };
}
