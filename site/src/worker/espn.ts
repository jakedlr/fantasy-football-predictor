/**
 * ESPN's weekly fantasy projections → KV `espn` (S11.5), the fallback for players with no `ffp`
 * Expected Points. Pure: no fetch, KV or clock (refresh.ts `runEspn` does those).
 *
 * Source: ESPN's fantasy API, view `kona_player_info` (research/leaderboard-projections.md B3).
 * `leaguedefaults/3` is "FFL PPR Scoring", so each line's `appliedTotal` is ESPN's PPR points,
 * which test/worker/espn.test.ts checks our conversion against.
 * Fixtures: site/test/fixtures/espn/projections-*.json (trimmed, recorded 2026-11-20).
 *
 * One response for all six positions is 2.9 MB, too much to parse in a free Worker's 10 ms of CPU
 * (research/leaderboard-live-spike.md, "ESPN projections"). So the positions are split over three
 * groups of about 0.7–0.9 MB, one request per run.
 */
import type { EspnStore, Expectation, IdMapStore, Position } from "./kv.js";
import { sleeperTeam, type Scoreboard, type Week } from "./scoreboard.js";
import { RULES, RULES_NAMES, scoreLine, tierPoints, type AnyStatLine, type Tier } from "./scoring.js";

export const ESPN_PROJECTIONS_HOST = "https://lm-api-reads.fantasy.espn.com";

/** ESPN's lineup slot ids (espn-api POSITION_MAP) and defaultPositionId for each position. */
const SLOT: Readonly<Record<Position, number>> = { QB: 0, RB: 2, WR: 4, TE: 6, K: 17, "D/ST": 16 };
const DEFAULT_POSITION: Readonly<Record<number, Position>> = { 1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST" };

export interface ProjectionGroup {
  positions: readonly Position[];
  /**
   * Players per request, best projected first. Week 11 (26 teams) had 86, 111 and 103 players
   * projected for 1 point or more in these groups; a week with no byes has about 23% more.
   */
  limit: number;
}

/** Balanced by bytes: QB+TE 0.70 MB, RB+K+D/ST 0.87 MB, WR 0.89 MB in Week 11. */
export const PROJECTION_GROUPS: readonly ProjectionGroup[] = [
  { positions: ["QB", "TE"], limit: 140 },
  { positions: ["RB", "K", "D/ST"], limit: 170 },
  { positions: ["WR"], limit: 160 },
];

/** The run's group, by the hour's third it starts in: the cron fires at minutes 9, 29 and 49. */
export function groupAt(now: Date): ProjectionGroup {
  return PROJECTION_GROUPS[Math.min(2, Math.floor(now.getUTCMinutes() / 20))]!;
}

/** ESPN's id for a week's projected line: statSourceId 1, statSplitTypeId 1, season, week. */
const lineId = (week: Week) => `11${week.season}${week.number}`;

/** The URL and `x-fantasy-filter` header for one group's projections in a regular-season week. */
export function projectionsRequest(week: Week, group: ProjectionGroup): { url: string; filter: string } {
  const id = lineId(week);
  const filter = {
    players: {
      filterSlotIds: { value: group.positions.map((p) => SLOT[p]) },
      filterStatsForTopScoringPeriodIds: { value: 1, additionalValue: [id] },
      // Rankings are 15 KB a player unfiltered; we don't use them.
      filterRanksForScoringPeriodIds: { value: [week.number] },
      filterRanksForRankTypes: { value: ["PPR"] },
      filterRanksForSlotIds: { value: [0] },
      sortAppliedStatTotal: { sortAsc: false, sortPriority: 1, value: id },
      limit: group.limit,
    },
  };
  return {
    url: `${ESPN_PROJECTIONS_HOST}/apis/v3/games/ffl/seasons/${week.season}/segments/0/leaguedefaults/3?view=kona_player_info&scoringPeriodId=${week.number}`,
    filter: JSON.stringify(filter),
  };
}

export class EspnProjectionsError extends Error {
  constructor(message: string) {
    super(`ESPN projections: ${message}`);
    this.name = "EspnProjectionsError";
  }
}

/** espn-api's PRO_TEAM_MAP (research B3): ESPN team id → ESPN abbreviation. 0 is a free agent. */
const PRO_TEAMS: Readonly<Record<number, string>> = {
  1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN",
  11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
  21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX",
  33: "BAL", 34: "HOU",
};

/**
 * ESPN stat id → Leaderboard stat key (espn-api PLAYER_STATS_MAP), checked against ESPN's own
 * PPR `appliedTotal` for every player of Week 11 (research/leaderboard-live-spike.md). Where
 * espn-api lists two ids for one stat, the one that matches `appliedTotal` is used: 24 not 40
 * (rushing yards), 42 not 61 (receiving yards), 53 not 41 (receptions); 40 and 61 are sometimes
 * doubled or 0. Offensive return TDs (101, 102) and fumble-recovery TDs (63) aren't ffp Scoring
 * Stats, so they are left out.
 */
const OFFENSE_IDS: Readonly<Record<string, string>> = {
  "3": "passing_yards", "4": "passing_tds", "20": "passing_interceptions", "19": "passing_2pt",
  "24": "rushing_yards", "25": "rushing_tds", "26": "rushing_2pt",
  "53": "receptions", "42": "receiving_yards", "43": "receiving_tds", "44": "receiving_2pt",
  "72": "fumbles_lost",
};
/**
 * ESPN's FG bands are under 40 (80), 40–49 (77) and 50+ (74; it includes 60+, since 83 = 80 + 77 + 74),
 * plus 60+ (201) when ESPN gives it (it didn't in Week 11). Under 40 spans Sleeper's three short
 * buckets and is put in fgm_30_39: every Scoring Rules option scores 0–39 as one tier (a test
 * checks), so the points are exact. 50+ less 60+ is fgm_50_59.
 */
const KICKER_IDS: Readonly<Record<string, string>> = {
  "80": "fgm_30_39", "77": "fgm_40_49", "201": "fgm_60p", "85": "fg_missed", "86": "pat_made", "88": "pat_missed",
};
const DST_IDS: Readonly<Record<string, string>> = {
  "99": "sacks", "95": "interceptions", "96": "fumble_recoveries", "106": "forced_fumbles", "98": "safeties",
  "97": "blocked_kicks", "103": "interception_return_tds", "104": "fumble_return_tds", "101": "kickoff_return_tds",
  "102": "punt_return_tds", "93": "blocked_kick_return_tds", "120": "points_allowed_all", "127": "yards_allowed",
};

/** ESPN's Points Allowed and Yards Allowed tier probabilities: stat id, band min, band max. */
const PA_BANDS: readonly (readonly [string, number, number | null])[] = [
  ["89", 0, 0], ["90", 1, 6], ["91", 7, 13], ["92", 14, 17], ["121", 18, 21], ["122", 22, 27],
  ["123", 28, 34], ["124", 35, 45], ["125", 46, null],
];
const YA_BANDS: readonly (readonly [string, number, number | null])[] = [
  ["128", 0, 99], ["129", 100, 199], ["130", 200, 299], ["131", 300, 349], ["132", 350, 399],
  ["133", 400, 449], ["134", 450, 499], ["135", 500, 549], ["136", 550, null],
];

const round = (x: number, places: number) => {
  const f = 10 ** places;
  return Math.round(x * f) / f;
};

/** A projected stat line → `Expectation.statMeans` (kv.ts): 3 decimals, zeros and negatives left out. */
export function espnStatMeans(position: Position, stats: Readonly<Record<string, number>>): Record<string, number> {
  const ids = position === "K" ? KICKER_IDS : position === "D/ST" ? DST_IDS : OFFENSE_IDS;
  const means: Record<string, number> = {};
  const put = (key: string, value: number) => {
    const r = round(value, 3);
    if (r > 0) means[key] = r;
  };
  for (const id in stats) {
    const value = stats[id];
    if (typeof value !== "number" || !Number.isFinite(value)) throw new EspnProjectionsError(`stat ${id} is ${JSON.stringify(value)}, not a number`);
    const key = ids[id];
    if (key !== undefined) put(key, value);
  }
  if (position === "K") put("fgm_50_59", (stats["74"] ?? 0) - (stats["201"] ?? 0));
  return sortKeys(means);
}

/**
 * Expected Points under every Scoring Rules option, to 2 decimals: `scoreLine` on the means,
 * except that a D/ST's Points and Yards Allowed tiers are scored from ESPN's tier probabilities
 * when it gives them. A tier of the mean overstates a D/ST by about 4 points (Steelers, Week 11:
 * mean PA 12.5 is the 3-point tier, but the expected tier points are 0.9). Each ESPN band lies
 * within one tier of every option; a band that straddles one falls back to the tier of the mean.
 */
export function espnExpected(position: Position, statMeans: Readonly<Record<string, number>>, stats: Readonly<Record<string, number>>): Record<string, number> {
  const line = (s: Record<string, number>): AnyStatLine =>
    position === "K" ? { kind: "kicker", stats: s } : position === "D/ST" ? { kind: "dst", stats: s } : { kind: "offense", stats: s };
  const expected: Record<string, number> = {};
  for (const name of RULES_NAMES) {
    const rules = RULES[name]!;
    let points = scoreLine(line({ ...statMeans }), rules);
    if (position === "D/ST") {
      const pa = rules.dst.points_allowed_tiers ?? [];
      const ya = rules.dst.yards_allowed_tiers ?? [];
      const ePa = bandPoints(stats, PA_BANDS, pa);
      if (ePa !== null) points += ePa - tierPoints(statMeans.points_allowed_all ?? 0, pa);
      const eYa = ya.length ? bandPoints(stats, YA_BANDS, ya) : null;
      if (eYa !== null) points += eYa - tierPoints(statMeans.yards_allowed ?? 0, ya);
    }
    expected[name] = round(points, 2);
  }
  return expected;
}

/** Σ P(band) × the band's tier points, or null if ESPN gave no bands or one straddles a tier. */
function bandPoints(stats: Readonly<Record<string, number>>, bands: typeof PA_BANDS, tiers: readonly Tier[]): number | null {
  let total = 0;
  let mass = 0;
  for (const [id, min, max] of bands) {
    const p = stats[id];
    if (!p) continue;
    if (tiers.some((t) => t.min > min && (max === null || t.min <= max))) return null;
    total += p * tierPoints(min, tiers);
    mass += p;
  }
  return Math.abs(mass - 1) < 0.02 ? total : null;
}

interface ProjectedLine {
  statSourceId?: unknown;
  statSplitTypeId?: unknown;
  scoringPeriodId?: unknown;
  seasonId?: unknown;
  stats?: unknown;
}

/** One ESPN player's projection for the week, converted and scored. */
export interface EspnProjection {
  espnId: string;
  name: string;
  position: Position;
  /** Sleeper's team code. */
  team: string;
  statMeans: Record<string, number>;
  expected: Record<string, number>;
}

/**
 * A `kona_player_info` response → the group's players with a non-empty projection for this week.
 * Players of other positions (eligible for a group's slot, e.g. a TE who can play QB) are left to
 * their own group; free agents (team 0) are left out.
 */
export function parseProjections(json: unknown, week: Week, positions: readonly Position[]): EspnProjection[] {
  const root = object(json, "response");
  if (!Array.isArray(root.players)) throw new EspnProjectionsError("players is missing or not a list");
  const out: EspnProjection[] = [];
  for (let i = 0; i < root.players.length; i++) {
    const at = `players[${i}]`;
    const entry = object(root.players[i], at);
    const player = object(entry.player, `${at}.player`);
    const espnId = entry.id;
    if (typeof espnId !== "number" || !Number.isInteger(espnId)) throw new EspnProjectionsError(`${at}.id is missing or not an integer`);
    const position = DEFAULT_POSITION[player.defaultPositionId as number];
    if (position === undefined || !positions.includes(position)) continue;
    const teamId = player.proTeamId;
    if (teamId === 0) continue;
    const abbreviation = typeof teamId === "number" ? PRO_TEAMS[teamId] : undefined;
    if (abbreviation === undefined) throw new EspnProjectionsError(`${at}: unknown team id ${String(teamId)}`);
    if (!Array.isArray(player.stats)) throw new EspnProjectionsError(`${at}.player.stats is missing or not a list`);
    // Matched on its fields rather than ESPN's composite id, whose layout for weeks 1–9 we haven't seen.
    const line = (player.stats as ProjectedLine[]).find(
      (s) => typeof s === "object" && s !== null && s.statSourceId === 1 && s.statSplitTypeId === 1 && s.scoringPeriodId === week.number && s.seasonId === week.season,
    );
    if (line === undefined) continue;
    const stats = object(line.stats, `${at} projected stats`) as Record<string, number>;
    const statMeans = espnStatMeans(position, stats);
    if (Object.keys(statMeans).length === 0) continue;
    out.push({
      espnId: String(espnId),
      name: typeof player.fullName === "string" && player.fullName ? player.fullName : String(espnId),
      position,
      team: sleeperTeam(abbreviation),
      statMeans,
      expected: espnExpected(position, statMeans, stats),
    });
  }
  return out;
}

export interface JoinCounts {
  /** Players from this response in the new store. */
  written: number;
  /** ESPN players with no Sleeper id in KV `idmap`. */
  unmapped: number;
  /** Players whose team has no Game this NFL Week (bye or canceled). */
  noGame: number;
}

/**
 * One group's projections merged into KV `espn`. A store for another season, season type or week
 * is replaced. Frozen at kickoff: a stored player whose Game has kicked off keeps his entry; one
 * with nothing stored takes the fresh entry (ESPN's projection stays pregame, research B3). A
 * stored player of this group missing from the response before his kickoff is dropped; other
 * groups' players are kept. `fetchedAt` is the stored one for the same week, else `now`: the caller
 * compares the serialized store with the stored text (one stringify, for the CPU budget) and sets
 * `fetchedAt` to now only when it writes.
 */
export function buildEspnStore(input: {
  stored: EspnStore | null;
  scoreboard: Scoreboard;
  projections: readonly EspnProjection[];
  idmap: IdMapStore | null;
  positions: readonly Position[];
  now: Date;
}): { store: EspnStore; counts: JoinCounts } {
  const { scoreboard, projections, idmap, positions, now } = input;
  const week = scoreboard.week;
  const sameWeek = (s: EspnStore | null): s is EspnStore =>
    s !== null && s.season === week.season && s.seasonType === week.seasonType && s.week === week.number;
  const stored = sameWeek(input.stored) ? input.stored : null;
  const kickoffs = new Map<string, string>();
  for (const g of scoreboard.games) {
    kickoffs.set(g.home, g.kickoff);
    kickoffs.set(g.away, g.kickoff);
  }
  const nowMs = now.getTime();
  const started = (kickoff: string) => Date.parse(kickoff) <= nowMs;

  const players: Record<string, Expectation> = {};
  for (const [key, e] of Object.entries(stored?.players ?? {})) {
    if (started(e.kickoff) || !positions.includes(e.position)) players[key] = e;
  }
  const counts: JoinCounts = { written: 0, unmapped: 0, noGame: 0 };
  for (const p of projections) {
    let key: string;
    if (p.position === "D/ST") key = `dst:${p.team}`;
    else {
      const sleeperId = idmap !== null && Object.hasOwn(idmap.espnToSleeper, p.espnId) ? idmap.espnToSleeper[p.espnId] : undefined;
      if (sleeperId === undefined) {
        counts.unmapped++;
        continue;
      }
      key = `sleeper:${sleeperId}`;
    }
    const kickoff = kickoffs.get(p.team);
    if (kickoff === undefined) {
      counts.noGame++;
      continue;
    }
    if (started(kickoff) && Object.hasOwn(players, key)) continue;
    players[key] = { name: p.name, team: p.team, position: p.position, kickoff, statMeans: p.statMeans, expected: p.expected };
    counts.written++;
  }

  const store: EspnStore = {
    schemaVersion: 1,
    season: week.season,
    seasonType: week.seasonType,
    week: week.number,
    fetchedAt: stored?.fetchedAt ?? instant(now),
    players: sortKeys(players),
  };
  return { store, counts };
}

function sortKeys<T>(o: Record<string, T>): Record<string, T> {
  const out: Record<string, T> = {};
  for (const k of Object.keys(o).sort()) out[k] = o[k]!;
  return out;
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new EspnProjectionsError(`${at} is missing or not an object`);
  return value as Record<string, unknown>;
}

/** RFC 3339 in UTC without milliseconds. */
export function instant(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}
