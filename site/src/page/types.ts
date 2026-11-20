/**
 * What GET /api/leaderboard returns, as typed by the page. The contract is
 * site/schema/leaderboard.schema.json; terms are in GLOSSARY.md (Leaderboard).
 */

export type GameStatus = "upcoming" | "live" | "final";
export type Position = "QB" | "RB" | "WR" | "TE" | "K" | "D/ST";

export interface Week {
  season: number;
  seasonType: "regular" | "postseason";
  number: number;
  start: string;
  end: string;
  firstKickoff: string;
  lastKickoff: string;
}

export interface Game {
  id: string;
  kickoff: string;
  status: GameStatus;
  home: string;
  away: string;
  homeScore: number | null;
  awayScore: number | null;
  /** Quarter; 5+ is overtime. null unless Live. */
  period: number | null;
  /** "7:32"; null unless Live. */
  clock: string | null;
}

export interface PointsEntry {
  /** Week Points: the ranking number. */
  week: number;
  /** Points So Far; null when Upcoming. */
  soFar: number | null;
  /** Expected Points, frozen at kickoff; null when none. */
  expected: number | null;
}

export interface Player {
  id: string;
  name: string;
  team: string;
  position: Position;
  gameId: string;
  out: boolean;
  expectedSource: "ffp" | "espn" | null;
  /** Keyed by Scoring Rules name. */
  points: Record<string, PointsEntry>;
}

export interface Leaderboard {
  schemaVersion: 1;
  updatedAt: string;
  expectedPointsAsOf: string | null;
  week: Week;
  scoringRules: string[];
  games: Game[];
  players: Player[];
}
