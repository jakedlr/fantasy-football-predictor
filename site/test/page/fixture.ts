/**
 * A generated Leaderboard, shaped as site/schema/leaderboard.schema.json, for the page's tests and for
 * the local preview server (site/scripts/serve.mjs). Deterministic for a given seed and clock.
 *
 * By default it is shaped like Sprint 10's data: `expected` null everywhere, so Week Points equal
 * Points So Far and Upcoming players show 0. `withExpected` fills Expected Points and Projected Totals
 * the way later sprints will.
 */
import type { Game, Leaderboard, Player, PointsEntry } from "../../src/page/types.js";

export interface FixtureOptions {
  /** About how many players to list; the default gives about 700. */
  players?: number;
  /** The viewer's "now"; Game Statuses and updatedAt are placed around it. */
  now?: number;
  /** Minutes between now and updatedAt. */
  ageMinutes?: number;
  withExpected?: boolean;
  seed?: number;
}

const RULES = ["ppr", "half-ppr", "standard", "espn-league"] as const;
const TEAMS = [
  "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
  "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
];
const NICK: Record<string, string> = {
  ARI: "Cardinals", ATL: "Falcons", BAL: "Ravens", BUF: "Bills", CAR: "Panthers", CHI: "Bears", CIN: "Bengals",
  CLE: "Browns", DAL: "Cowboys", DEN: "Broncos", DET: "Lions", GB: "Packers", HOU: "Texans", IND: "Colts",
  JAX: "Jaguars", KC: "Chiefs", LAC: "Chargers", LAR: "Rams", LV: "Raiders", MIA: "Dolphins", MIN: "Vikings",
  NE: "Patriots", NO: "Saints", NYG: "Giants", NYJ: "Jets", PHI: "Eagles", PIT: "Steelers", SEA: "Seahawks",
  SF: "49ers", TB: "Buccaneers", TEN: "Titans", WAS: "Commanders",
};
const FIRST = "ABCDEGJKLMNRSTW";
const LAST = [
  "Abbott", "Barnes", "Caldwell", "Dawson", "Ellison", "Fairley", "Garrity", "Holloway", "Ingram", "Jarrett",
  "Kessler", "Lindqvist", "Mercer", "Nwosu", "Okafor", "Pruitt", "Quarles", "Rhodes", "Saunders", "Tatum",
  "Underwood", "Vance", "Whitfield", "Yancey", "Zeller", "Ashby", "Brannon", "Crowder", "Delacroix", "Easley",
  "Fontaine", "Greer", "Hutchins", "Iverson", "Joiner", "Kincaid", "Lockett", "Maddox", "Northcutt", "Oduya",
  "Pickett", "Redmond", "Sterling", "Thornton", "Upshaw", "Vickers", "Wakefield", "Young", "St. Clair", "O'Neal",
];
/** Per team: how many of each position get listed, and a rough PPR mean. */
const ROSTER: [Player["position"], number, number][] = [
  ["QB", 2, 17], ["RB", 4, 11], ["WR", 6, 9], ["TE", 3, 6], ["K", 1, 8], ["D/ST", 1, 7],
];

function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13; s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5; s >>>= 0;
    return s / 2 ** 32;
  };
}
const r1 = (x: number) => Math.round(x * 10) / 10;
const iso = (t: number) => new Date(Math.floor(t / 60_000) * 60_000).toISOString().replace(".000Z", "Z");

export function generateLeaderboard(opts: FixtureOptions = {}): Leaderboard {
  const now = opts.now ?? Date.parse("2026-11-22T21:12:00Z");
  const rand = rng(opts.seed ?? 12);
  const target = opts.players ?? 700;
  const H = 3_600_000;

  // 16 Games: 1 Thursday Final, 9 early Sunday (5 Final, 4 Live), 4 late Live/Upcoming, Sunday and Monday night.
  const slots: { kickoff: number; status: Game["status"] }[] = [
    { kickoff: now - 68 * H, status: "final" },
    ...Array.from({ length: 5 }, () => ({ kickoff: now - 3.2 * H, status: "final" as const })),
    ...Array.from({ length: 4 }, () => ({ kickoff: now - 2.9 * H, status: "live" as const })),
    ...Array.from({ length: 2 }, () => ({ kickoff: now - 0.1 * H, status: "live" as const })),
    ...Array.from({ length: 2 }, () => ({ kickoff: now + 0.25 * H, status: "upcoming" as const })),
    { kickoff: now + 4.1 * H, status: "upcoming" },
    { kickoff: now + 28 * H, status: "upcoming" },
  ];
  const games: Game[] = slots.map((slot, i) => {
    const away = TEAMS[2 * i]!;
    const home = TEAMS[2 * i + 1]!;
    const live = slot.status === "live";
    const early = slot.kickoff < now - H;
    const period = live ? (early ? 4 : 1) : null;
    const clock = live ? `${Math.floor(rand() * 15)}:${String(Math.floor(rand() * 60)).padStart(2, "0")}` : null;
    const score = () => (slot.status === "upcoming" ? null : Math.floor(rand() * (live && !early ? 8 : 35)));
    return {
      id: String(401772900 + i), kickoff: iso(slot.kickoff), status: slot.status, home, away,
      homeScore: score(), awayScore: score(), period, clock,
    };
  });

  const perTeam = ROSTER.reduce((n, [, count]) => n + count, 0);
  const scale = target / (perTeam * games.length * 2);
  const players: Player[] = [];
  let n = 0;
  for (const game of games) {
    const left = game.status === "final" ? 0 : game.status === "upcoming" ? 1 : (4 - (game.period ?? 1)) / 4 + 0.15;
    for (const team of [game.away, game.home]) {
      for (const [position, count, mean] of ROSTER) {
        const listed = Math.max(1, Math.round(count * scale));
        for (let k = 0; k < listed; k++) {
          n++;
          const name = position === "D/ST" ? `${NICK[team]} D/ST`
            : `${FIRST[Math.floor(rand() * FIRST.length)]}. ${LAST[Math.floor(rand() * LAST.length)]}`;
          const out = game.status === "upcoming" && rand() < 0.03;
          const recs = position === "WR" || position === "TE" || position === "RB" ? Math.floor(rand() * 7) : 0;
          const exp = out ? null : r1(mean * (1.3 - k * 0.25) * (0.6 + rand() * 0.8));
          const rawSoFar = game.status === "upcoming" ? null : r1(Math.max(-2, mean * (1.4 - k * 0.3) * rand() * 1.6 * (1 - left)) + recs);
          const points: Record<string, PointsEntry> = {};
          for (const rule of RULES) {
            const recPts = rule === "half-ppr" ? 0.5 : rule === "standard" ? 1 : 0;
            const soFar = rawSoFar == null ? null : r1(rawSoFar - recPts * recs);
            const expected = opts.withExpected && exp != null ? r1(Math.max(0, exp - recPts * recs)) : null;
            let week: number;
            if (out) week = soFar ?? 0;
            else if (game.status === "final") week = soFar ?? 0;
            else if (game.status === "live") week = r1((soFar ?? 0) + (expected ?? 0) * left);
            else week = expected ?? 0;
            points[rule] = { week, soFar, expected };
          }
          const source: Player["expectedSource"] = !opts.withExpected || out ? null : rand() < 0.12 ? "espn" : "ffp";
          players.push({
            id: position === "D/ST" ? `dst:${team}` : `sleeper:${10000 + n}`,
            name, team, position, gameId: game.id, out, expectedSource: source, points,
          });
        }
      }
    }
  }

  const kickoffs = games.map((g) => Date.parse(g.kickoff));
  return {
    schemaVersion: 1,
    updatedAt: iso(now - (opts.ageMinutes ?? 3) * 60_000),
    expectedPointsAsOf: opts.withExpected ? iso(now - 40 * 60_000) : null,
    week: {
      season: 2026, seasonType: "regular", number: 12,
      start: iso(now - 100 * H), end: iso(now + 60 * H),
      firstKickoff: iso(Math.min(...kickoffs)), lastKickoff: iso(Math.max(...kickoffs)),
    },
    scoringRules: [...RULES],
    games,
    players,
  };
}
