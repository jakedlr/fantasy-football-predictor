/**
 * The TypeScript port of `ffp.scoring`'s `score`, `score_kicker` and `score_dst` (ADR 0005).
 *
 * Plain numbers only, no Monte Carlo sample arrays. The rules are data in the shape of
 * `contracts/scoring-rules.json`, exported from Python; `test/worker/scoring.test.ts` holds this
 * port to every case in `contracts/scoring-cases.json`. A rules change is made in Python and
 * re-exported, never here.
 */
import contract from "../../contracts/scoring-rules.json";

/** The offensive Scoring Stats (QB/RB/WR/TE), as in `ffp.scoring.SCORING_STATS`. */
export const OFFENSE_STATS = [
  "passing_yards",
  "passing_tds",
  "passing_interceptions",
  "passing_2pt",
  "rushing_yards",
  "rushing_tds",
  "rushing_2pt",
  "receptions",
  "receiving_yards",
  "receiving_tds",
  "receiving_2pt",
  "fumbles_lost",
] as const;
export type OffenseStat = (typeof OFFENSE_STATS)[number];

/** A kicker's per-unit Scoring Stats; FGs made score by distance (`fg_made_by_distance`). */
export const KICKER_STATS = ["pat_made", "pat_missed", "fg_missed"] as const;
export type KickerStat = (typeof KICKER_STATS)[number];

/** A D/ST's per-unit Scoring Stats; Points Allowed and Yards Allowed score by tier. */
export const DST_STATS = [
  "sacks",
  "interceptions",
  "fumble_recoveries",
  "forced_fumbles",
  "safeties",
  "blocked_kicks",
  "interception_return_tds",
  "fumble_return_tds",
  "kickoff_return_tds",
  "punt_return_tds",
  "blocked_kick_return_tds",
] as const;
export type DstStat = (typeof DST_STATS)[number];

/** What Scoring Rules may leave out of Points Allowed (`ffp.scoring.POINTS_ALLOWED_EXCLUSIONS`). */
export type PointsAllowedExclusion = "opponent_defensive_tds" | "opponent_return_tds";

/** A whole-number tier, `max` inclusive; only the last tier may be open-ended (`max: null`). */
export interface Tier {
  min: number;
  max: number | null;
  points: number;
}

export type KickerRules = Partial<Record<KickerStat, number>> & { fg_made_by_distance?: Tier[] };
export type DstRules = Partial<Record<DstStat, number>> & {
  points_allowed_excludes?: PointsAllowedExclusion[] | string[];
  points_allowed_tiers?: Tier[];
  yards_allowed_tiers?: Tier[];
};

/** One Scoring Rules option, as in `contracts/scoring-rules.json`'s `rules`. */
export interface ScoringRules {
  offense: Partial<Record<OffenseStat, number>>;
  kicker: KickerRules;
  dst: DstRules;
  assumptions?: string[];
}

export type OffenseStatLine = Partial<Record<OffenseStat, number>>;
/** Sleeper's FGs-made distance buckets (`contracts/scoring-rules.json` sleeperFgBuckets), shortest first. */
export const FG_BUCKET_KEYS = ["fgm_0_19", "fgm_20_29", "fgm_30_39", "fgm_40_49", "fgm_50_59", "fgm_60p"] as const;
export type FgBucket = (typeof FG_BUCKET_KEYS)[number];

/**
 * `fg_made_yards`: the distance of each FG made. `fgm_*`: FGs made per Sleeper bucket, each scored
 * at its bucket's min; may be fractional (a Projected Total: made so far + mean × fraction left).
 * A line uses one or the other; both count if both are given.
 */
export type KickerStatLine = Partial<Record<KickerStat | FgBucket, number>> & { fg_made_yards?: readonly number[] };
export type DstStatLine = Partial<Record<DstStat, number>> & {
  points_allowed_all?: number;
  opponent_defensive_tds_points?: number;
  opponent_return_tds_points?: number;
  yards_allowed?: number;
};

interface ScoringContract {
  default: string;
  rules: Record<string, ScoringRules>;
  sleeperFgBuckets: Record<string, { min: number; max: number | null }>;
}
const CONTRACT = contract as unknown as ScoringContract;

/** The Leaderboard's Scoring Rules options, by name. */
export const RULES: Readonly<Record<string, ScoringRules>> = CONTRACT.rules;
/** The option names, default (`ppr`) first. */
export const RULES_NAMES: readonly string[] = [CONTRACT.default, ...Object.keys(CONTRACT.rules).filter((n) => n !== CONTRACT.default)];

/** Fantasy points for a QB/RB/WR/TE stat line; missing stats count as 0. */
export function score(line: OffenseStatLine, rules: ScoringRules): number {
  let total = 0;
  for (const stat of OFFENSE_STATS) {
    const value = line[stat];
    if (value) total += (rules.offense[stat] ?? 0) * value;
  }
  return total;
}

/**
 * A value's tier points, as Python's `_tier_points`: the last tier whose min the value reaches;
 * below the first tier, the first tier's points; above a closed last tier, 0. A fractional value
 * between two tiers (17.5 between 14–17 and 18–27) falls in the lower one.
 */
export function tierPoints(value: number, tiers: readonly Tier[]): number {
  const n = tiers.length;
  if (n === 0) return 0;
  const top = tiers[n - 1]!.max;
  if (top !== null && value > top) return 0;
  let i = n - 1;
  while (i > 0 && tiers[i]!.min > value) i--;
  return tiers[i]!.points;
}

/** A kicker's fantasy points: PATs and misses per unit, each FG made by its distance's tier. */
export function scoreKicker(line: KickerStatLine, rules: ScoringRules): number {
  const k = rules.kicker;
  let total = 0;
  for (const stat of KICKER_STATS) {
    const value = line[stat];
    if (value) total += (k[stat] ?? 0) * value;
  }
  const tiers = k.fg_made_by_distance ?? [];
  for (const yards of line.fg_made_yards ?? []) total += tierPoints(yards, tiers);
  for (const [key, min] of FG_BUCKET_MINS) {
    const made = line[key];
    if (made) total += made * tierPoints(min, tiers);
  }
  return total;
}

/** The D/ST's Points Allowed: `points_allowed_all` less the opponent's TD points the rules leave out. */
export function pointsAllowed(line: DstStatLine, rules: ScoringRules): number {
  let pa = line.points_allowed_all ?? 0;
  for (const what of rules.dst.points_allowed_excludes ?? []) {
    pa -= line[`${what}_points` as "opponent_defensive_tds_points" | "opponent_return_tds_points"] ?? 0;
  }
  return pa;
}

/** A D/ST's fantasy points: the per-unit stats plus the Points Allowed and Yards Allowed tiers. */
export function scoreDst(line: DstStatLine, rules: ScoringRules): number {
  const d = rules.dst;
  let total = 0;
  for (const stat of DST_STATS) {
    const value = line[stat];
    if (value) total += (d[stat] ?? 0) * value;
  }
  total += tierPoints(pointsAllowed(line, rules), d.points_allowed_tiers ?? []);
  if (d.yards_allowed_tiers?.length) total += tierPoints(line.yards_allowed ?? 0, d.yards_allowed_tiers);
  return total;
}

const FG_BUCKETS = Object.entries(CONTRACT.sleeperFgBuckets);
const FG_BUCKET_MINS = FG_BUCKET_KEYS.map((key) => {
  const bucket = CONTRACT.sleeperFgBuckets[key];
  if (!bucket) throw new Error(`contracts/scoring-rules.json: no Sleeper FG bucket ${key}`);
  return [key, bucket.min] as const;
});

/**
 * The FGs made in a Sleeper stats row (`fgm_20_29`-style buckets), each at its bucket's min. Exact
 * while no bucket straddles a tier edge, which the contract's exporter checks. Totals (`fgm`,
 * `fgm_yds`, `fgm_lng`) and the overlapping `fgm_50p` are not buckets and are ignored.
 */
export function fgMadeYardsFromSleeperBuckets(stats: Readonly<Record<string, number | null | undefined>>): number[] {
  const yards: number[] = [];
  for (const [key, { min }] of FG_BUCKETS) {
    const made = stats[key];
    for (let i = 0; made && i < made; i++) yards.push(min);
  }
  return yards;
}
