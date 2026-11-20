/**
 * When the page fetches /api/leaderboard again (PLAN.md, "Hosting"). Every Function call counts
 * toward Cloudflare's free 100,000 requests a day, so the page polls only while it matters:
 *
 * - every 5 minutes while the tab is visible and a Game is Live or kicks off within 10 minutes;
 * - otherwise once, 10 minutes before the next kickoff (so polling is running when it starts);
 * - never while the tab is hidden; on being shown again, at once if the last fetch is over 5 minutes old.
 *
 * Pure: the caller passes the clock and visibility and owns the timer.
 */
import type { Game } from "./types.js";

export const POLL_MS = 5 * 60_000;
export const KICKOFF_LEAD_MS = 10 * 60_000;
/** A Game still Upcoming this long after kickoff is taken as postponed and stops the polling. */
export const STUCK_AFTER_MS = 3 * 3_600_000;
/** The longest single wait, so a laptop that slept through its timer wakes to a fresh plan. */
export const MAX_WAIT_MS = 6 * 3_600_000;

export interface PollInput {
  games: Pick<Game, "status" | "kickoff">[];
  now: number;
  /** When the last fetch started, successful or not. */
  lastFetchAt: number;
  visible: boolean;
}

/** Milliseconds until the next fetch, or null for none until the tab is shown again. */
export function nextFetchIn({ games, now, lastFetchAt, visible }: PollInput): number | null {
  if (!visible) return null;
  let nextKickoff = Infinity;
  let active = false;
  for (const g of games) {
    if (g.status === "live") active = true;
    else if (g.status === "upcoming") {
      const k = Date.parse(g.kickoff);
      if (k - now <= KICKOFF_LEAD_MS && now - k < STUCK_AFTER_MS) active = true;
      else if (k > now) nextKickoff = Math.min(nextKickoff, k);
    }
  }
  if (active) return Math.max(0, lastFetchAt + POLL_MS - now);
  if (nextKickoff === Infinity) return null;
  return Math.min(nextKickoff - KICKOFF_LEAD_MS - now, MAX_WAIT_MS);
}

/** On the tab being shown again: fetch at once if the last fetch is more than 5 minutes old. */
export function refetchOnShow(lastFetchAt: number, now: number): boolean {
  return now - lastFetchAt > POLL_MS;
}
