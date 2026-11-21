/**
 * Sleeper's weekly stats rows → Leaderboard players with `ffp.scoring` stat lines (ADR 0006).
 *
 * The endpoint (`api.sleeper.com/stats/nfl/<season>/<week>?season_type=regular&position[]=...`)
 * answers one row per player who is active this NFL Week. QB/RB/WR/TE rows become offensive stat
 * lines, K rows kicker lines (FGs made by Sleeper's distance buckets) and DEF rows D/ST lines.
 *
 * It runs on every 5-minute refresh inside a free Worker's 10 ms of CPU, for about 2,000 rows:
 * one pass, and only the keys the Leaderboard needs are copied out of each row. D/ST lines get a
 * second look at the (at most 32) DEF rows after the pass, for the opponent's TDs.
 */
import {
  FG_BUCKET_KEYS,
  type DstStat,
  type DstStatLine,
  type FgBucket,
  type KickerStat,
  type KickerStatLine,
  type OffenseStat,
  type OffenseStatLine,
} from "./scoring.js";

/** Sleeper's stats keys for each offensive Scoring Stat. Checked against Sleeper's `pts_ppr`. */
export const SLEEPER_OFFENSE_KEYS = {
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
} as const satisfies Record<string, OffenseStat>;
const OFFENSE_KEY_PAIRS = Object.entries(SLEEPER_OFFENSE_KEYS) as [keyof typeof SLEEPER_OFFENSE_KEYS, OffenseStat][];

/**
 * Sleeper's K keys for each FG bucket and kicker Scoring Stat. Checked on 2026 Week 10 against
 * Sleeper's `pts_std` (28 of 28 kickers, but for a kicker's own forced fumble, `st_ff`, which ffp
 * doesn't score) and nflverse play-by-play (28 of 28 teams: FGs made by bucket, misses, PATs).
 * On every row `xpmiss` is xpa − xpm and `fgmiss` is fga − fgm (a blocked kick is a miss).
 * The totals `fgm`, `fgm_yds`, `fgm_lng` and the overlapping `fgm_50p` are not read.
 */
export const SLEEPER_KICKER_KEYS = {
  ...(Object.fromEntries(FG_BUCKET_KEYS.map((k) => [k, k])) as { [B in FgBucket]: B }),
  xpm: "pat_made",
  xpmiss: "pat_missed",
  fgmiss: "fg_missed",
} as const satisfies Record<string, KickerStat | FgBucket>;
const KICKER_KEY_PAIRS = Object.entries(SLEEPER_KICKER_KEYS) as [string, KickerStat | FgBucket][];

/**
 * Sleeper's DEF keys for each D/ST Scoring Stat; two keys feeding one stat are added. Checked on
 * 2026 Week 10 against Sleeper's `pts_std` and nflverse (research/leaderboard-live-spike.md,
 * "K and D/ST from Sleeper"):
 * - `ff` and `fum_rec` leave out special teams (`def_st_ff`, `def_st_fum_rec`). ffp counts every
 *   forced fumble and recovery, so they are added (nflverse agrees on 25 of 28 teams; the other
 *   three are credit disputes between the two sources).
 * - `def_td` is INT and fumble return TDs together, `def_st_td` kickoff, punt and blocked-kick
 *   return TDs together: Sleeper doesn't split them. They go in `interception_return_tds` and
 *   `kickoff_return_tds`, which scores exactly while every Scoring Rules option scores each kind
 *   alike (all four do; a test checks it).
 * - `yds_allow` is net of sack yards, as ffp's Yards Allowed (28 of 28 teams).
 * - `pts_allow` is read separately: see `dstStatLine`.
 * Not read: the `pts_allow_*` and `yds_allow_*` buckets (the scorer tiers the counts), `td`,
 * `misc_td`, `def_2pt` and `fum_rec_ez_tds` (not ffp Scoring Stats, nor in Sleeper's own points).
 */
export const SLEEPER_DST_KEYS = {
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
} as const satisfies Record<string, DstStat | "yards_allowed">;
const DST_KEY_PAIRS = Object.entries(SLEEPER_DST_KEYS) as [string, DstStat | "yards_allowed"][];
/** A TD without its try: what Sleeper's `pts_allow` leaves out per opponent defensive TD. */
const TD_POINTS = 6;

export type OffensePosition = "QB" | "RB" | "WR" | "TE";

/** A QB/RB/WR/TE from one Sleeper row. */
export interface SleeperOffensePlayer {
  kind: "offense";
  /** `sleeper:<player_id>`, the Leaderboard's player id. */
  id: string;
  name: string;
  team: string;
  position: OffensePosition;
  /**
   * Sleeper's own game id: season, season type, week and a game number (`202611113`). It is not
   * ESPN's event id, so Games are joined to players by team, not by this.
   */
  sleeperGameId: string | null;
  /** Only the stats Sleeper reported; missing ones count as 0 (`score`). Empty when none. */
  stats: OffenseStatLine;
}
/** A kicker from one Sleeper row (S11.1): FGs made by Sleeper's `fgm_*` buckets, PATs and misses. */
export interface SleeperKickerPlayer {
  kind: "kicker";
  id: string;
  name: string;
  team: string;
  position: "K";
  sleeperGameId: string | null;
  stats: KickerStatLine;
}

/** A D/ST from one Sleeper DEF row (S11.1). `id` is `dst:<TEAM>`; `name` as "Ravens D/ST". */
export interface SleeperDstPlayer {
  kind: "dst";
  id: string;
  name: string;
  team: string;
  position: "D/ST";
  sleeperGameId: string | null;
  stats: DstStatLine;
}

export type SleeperPlayer = SleeperOffensePlayer | SleeperKickerPlayer | SleeperDstPlayer;

/** Rows not turned into players: those with no QB/RB/WR/TE, K or DEF position (IDP-only players). */
export interface SkippedRows {
  other: number;
}

export interface SleeperWeek {
  players: SleeperPlayer[];
  skipped: SkippedRows;
}

/** Sleeper's response wasn't the shape this module reads; the caller keeps the last good data. */
export class SleeperShapeError extends Error {
  override name = "SleeperShapeError";
}

interface SleeperRow {
  player_id?: unknown;
  team?: unknown;
  opponent?: unknown;
  game_id?: unknown;
  stats?: Record<string, unknown> | null;
  player?: { first_name?: unknown; last_name?: unknown; position?: unknown; fantasy_positions?: unknown } | null;
}
type Stats = SleeperRow["stats"];

const OFFENSE: ReadonlySet<string> = new Set<OffensePosition>(["QB", "RB", "WR", "TE"]);

/**
 * The row's Leaderboard position: `player.position` when it's QB/RB/WR/TE, K or DEF; otherwise its
 * first QB/RB/WR/TE fantasy position (a FB is listed at RB, a DB with WR eligibility at WR); else null.
 */
function positionOf(player: NonNullable<SleeperRow["player"]>): OffensePosition | "K" | "DEF" | null {
  const pos = player.position;
  if (typeof pos === "string" && (OFFENSE.has(pos) || pos === "K" || pos === "DEF")) return pos as OffensePosition | "K" | "DEF";
  const fantasy = player.fantasy_positions;
  if (Array.isArray(fantasy)) {
    for (const p of fantasy) if (typeof p === "string" && OFFENSE.has(p)) return p as OffensePosition;
  }
  return null;
}

/** The numbers among `pairs`' Sleeper keys, copied to their stat names. */
function copyStats<S extends string>(stats: Stats, pairs: readonly (readonly [string, S])[]): Partial<Record<S, number>> {
  const line: Partial<Record<S, number>> = {};
  if (!stats) return line;
  for (const [key, stat] of pairs) {
    const value = stats[key];
    if (typeof value === "number") line[stat] = value;
  }
  return line;
}

/** Number `key` of a row's stats, or 0. */
function num(stats: Stats, key: string): number {
  const v = stats?.[key];
  return typeof v === "number" ? v : 0;
}

/**
 * A DEF row's D/ST line, before the opponent's TDs are known. `points_allowed_all` starts at
 * Sleeper's `pts_allow`: every point the opponent scored, less 6 per INT or fumble return TD (the
 * try after it stays in). That held for all 28 teams in Week 10 against nflverse, and for 27 of 29
 * teams' season totals. `addOpponentTds` adds the 6s back. Points and Yards Allowed are set only
 * when Sleeper sent them, so a DEF row with no stats yet is an empty line ("no stat recorded",
 * leaderboard.ts); the scorer reads a missing points_allowed_all as 0.
 */
function dstStatLine(stats: Stats): DstStatLine {
  const line: DstStatLine = {};
  if (stats) {
    for (const [key, stat] of DST_KEY_PAIRS) {
      const value = stats[key];
      if (typeof value === "number") line[stat] = (line[stat] ?? 0) + value;
    }
    const allowed = stats.pts_allow;
    if (typeof allowed === "number") line.points_allowed_all = allowed;
  }
  return line;
}

/**
 * The opponent's defensive and return TDs, from its own DEF row: points_allowed_all gets back the
 * 6 per defensive TD that Sleeper's pts_allow left out, and the opponent's TD points are recorded
 * for Scoring Rules that leave them out. Sleeper's rows can't say whether the try after such a TD
 * was good, so these are 6 per TD: a rule leaving them out leaves out the TD but not its try, as
 * Sleeper does (ffp's nflverse lines leave out the try too). No opponent row yet means no TDs.
 */
function addOpponentTds(dsts: readonly DstPending[], tdsByTeam: ReadonlyMap<string, readonly [number, number]>): void {
  for (const { player, opponent } of dsts) {
    const [defensive, returns] = tdsByTeam.get(opponent) ?? [0, 0];
    const line = player.stats;
    if (defensive) {
      line.points_allowed_all = (line.points_allowed_all ?? 0) + TD_POINTS * defensive;
      line.opponent_defensive_tds_points = TD_POINTS * defensive;
    }
    if (returns) line.opponent_return_tds_points = TD_POINTS * returns;
  }
}
interface DstPending {
  player: SleeperDstPlayer;
  opponent: string;
}

const nameOf = (first: string, last: string, id: string) => (first && last ? `${first} ${last}` : first || last || id);

/**
 * Sleeper's weekly stats response → Leaderboard players. A player with no stats, or only
 * `gms_active`-style keys, is kept with an empty stat line (0 points so far), not dropped. Throws
 * `SleeperShapeError` when the response or a row lacks what the Leaderboard needs.
 */
export function sleeperStatsToPlayers(rows: readonly unknown[]): SleeperWeek {
  if (!Array.isArray(rows)) throw new SleeperShapeError("Sleeper stats: expected a list of rows");
  const players: SleeperPlayer[] = [];
  const skipped: SkippedRows = { other: 0 };
  const dsts: DstPending[] = [];
  /** Each team's [defensive TDs, return TDs], from its DEF row. */
  const tdsByTeam = new Map<string, readonly [number, number]>();
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i] as SleeperRow;
    const player = row?.player;
    if (!player || typeof player !== "object") throw new SleeperShapeError(`Sleeper stats row ${i}: no player`);
    const position = positionOf(player);
    if (position === null) {
      skipped.other++;
      continue;
    }
    const { player_id: id, team, game_id: gameId, stats } = row;
    if (typeof id !== "string" || !id) throw new SleeperShapeError(`Sleeper stats row ${i}: no player_id`);
    if (typeof team !== "string" || !team) throw new SleeperShapeError(`Sleeper stats row ${i} (${id}): no team`);
    const first = typeof player.first_name === "string" ? player.first_name : "";
    const last = typeof player.last_name === "string" ? player.last_name : "";
    const sleeperGameId = typeof gameId === "string" && gameId ? gameId : null;
    switch (position) {
      case "DEF": {
        // A DEF row's player_id is its team, and its last name the nickname ("Commanders").
        const dst: SleeperDstPlayer = { kind: "dst", id: `dst:${team}`, name: `${last || team} D/ST`, team, position: "D/ST", sleeperGameId, stats: dstStatLine(stats) };
        players.push(dst);
        dsts.push({ player: dst, opponent: typeof row.opponent === "string" ? row.opponent : "" });
        tdsByTeam.set(team, [num(stats, "def_td"), num(stats, "def_st_td")]);
        break;
      }
      case "K":
        players.push({ kind: "kicker", id: `sleeper:${id}`, name: nameOf(first, last, id), team, position, sleeperGameId, stats: copyStats(stats, KICKER_KEY_PAIRS) });
        break;
      default:
        players.push({ kind: "offense", id: `sleeper:${id}`, name: nameOf(first, last, id), team, position, sleeperGameId, stats: copyStats(stats, OFFENSE_KEY_PAIRS) });
    }
  }
  addOpponentTds(dsts, tdsByTeam);
  return { players, skipped };
}
