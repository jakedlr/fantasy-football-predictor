/**
 * Sleeper's weekly stats rows → Leaderboard players with `ffp.scoring` stat lines (ADR 0006).
 *
 * The endpoint (`api.sleeper.com/stats/nfl/<season>/<week>?season_type=regular&position[]=...`)
 * answers one row per player who is active this NFL Week. Sprint 10 converts QB/RB/WR/TE rows;
 * K and DEF rows are counted in `skipped` until Sprint 11 adds their stat lines (see the seam in
 * `sleeperStatsToPlayers`).
 *
 * It runs on every 5-minute refresh inside a free Worker's 10 ms of CPU, for about 2,000 rows:
 * one pass, and only the keys the Leaderboard needs are copied out of each row.
 */
import type { DstStatLine, KickerStatLine, OffenseStat, OffenseStatLine } from "./scoring.js";

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

export type OffensePosition = "QB" | "RB" | "WR" | "TE";

/** A QB/RB/WR/TE from one Sleeper row. `kind` leaves room for Sprint 11's kicker and D/ST players. */
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

/** Rows not turned into players: K and DEF until Sprint 11, and anything else (IDP-only players). */
export interface SkippedRows {
  K: number;
  DEF: number;
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
  game_id?: unknown;
  stats?: Record<string, unknown> | null;
  player?: { first_name?: unknown; last_name?: unknown; position?: unknown; fantasy_positions?: unknown } | null;
}

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

function offenseStatLine(stats: SleeperRow["stats"]): OffenseStatLine {
  const line: OffenseStatLine = {};
  if (!stats) return line;
  for (const [key, stat] of OFFENSE_KEY_PAIRS) {
    const value = stats[key];
    if (typeof value === "number") line[stat] = value;
  }
  return line;
}

/**
 * Sleeper's weekly stats response → Leaderboard players. A player with no stats, or only
 * `gms_active`-style keys, is kept with an empty stat line (0 points so far), not dropped. Throws
 * `SleeperShapeError` when the response or a row lacks what the Leaderboard needs.
 */
export function sleeperStatsToPlayers(rows: readonly unknown[]): SleeperWeek {
  if (!Array.isArray(rows)) throw new SleeperShapeError("Sleeper stats: expected a list of rows");
  const players: SleeperPlayer[] = [];
  const skipped: SkippedRows = { K: 0, DEF: 0, other: 0 };
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i] as SleeperRow;
    const player = row?.player;
    if (!player || typeof player !== "object") throw new SleeperShapeError(`Sleeper stats row ${i}: no player`);
    const position = positionOf(player);
    switch (position) {
      case "K":
      case "DEF":
        // Sprint 11: kicker lines (scoring.fgMadeYardsFromSleeperBuckets) and D/ST lines go here.
        skipped[position]++;
        continue;
      case null:
        skipped.other++;
        continue;
    }
    const { player_id: id, team, game_id: gameId } = row;
    if (typeof id !== "string" || !id) throw new SleeperShapeError(`Sleeper stats row ${i}: no player_id`);
    if (typeof team !== "string" || !team) throw new SleeperShapeError(`Sleeper stats row ${i} (${id}): no team`);
    const first = typeof player.first_name === "string" ? player.first_name : "";
    const last = typeof player.last_name === "string" ? player.last_name : "";
    players.push({
      kind: "offense",
      id: `sleeper:${id}`,
      name: first && last ? `${first} ${last}` : first || last || id,
      team,
      position,
      sleeperGameId: typeof gameId === "string" && gameId ? gameId : null,
      stats: offenseStatLine(row.stats),
    });
  }
  return { players, skipped };
}
