/**
 * Times on the Leaderboard, in the viewer's timezone and language (PLAN.md, "Time"). Every function
 * takes its formatters and "now" from the caller so tests can pin the locale, zone and clock.
 */
import type { Game, Week } from "./types.js";

export interface TimeOptions {
  /** BCP 47 locale; undefined means the browser's. */
  locale?: string;
  /** IANA zone; undefined means the browser's. */
  timeZone?: string;
}

export interface TimeFormat {
  /** "1:05 PM EST" */
  clockTime(iso: string): string;
  /** "now", "3 min. ago", "2 hr. ago", "yesterday" */
  ago(iso: string, now: number): string;
  /** "Thu, Nov 19 – Sun, Nov 23": the days of the first and last Game. */
  weekRange(week: Pick<Week, "firstKickoff" | "lastKickoff">): string;
  /** "Sun 1:00 PM" */
  kickoff(iso: string): string;
}

export function makeTimeFormat(opts: TimeOptions = {}): TimeFormat {
  const { locale, timeZone } = opts;
  const clock = new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit", timeZoneName: "short", timeZone });
  const day = new Intl.DateTimeFormat(locale, { weekday: "short", month: "short", day: "numeric", timeZone });
  const kick = new Intl.DateTimeFormat(locale, { weekday: "short", hour: "numeric", minute: "2-digit", timeZone });
  const rel = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "short" });
  return {
    clockTime: (iso) => clock.format(Date.parse(iso)),
    ago(iso, now) {
      const minutes = Math.floor(Math.max(0, now - Date.parse(iso)) / 60_000);
      if (minutes < 1) return rel.format(0, "second");
      if (minutes < 60) return rel.format(-minutes, "minute");
      const hours = Math.floor(minutes / 60);
      if (hours < 24) return rel.format(-hours, "hour");
      return rel.format(-Math.floor(hours / 24), "day");
    },
    weekRange: (week) => day.formatRange(Date.parse(week.firstKickoff), Date.parse(week.lastKickoff)),
    kickoff: (iso) => kick.format(Date.parse(iso)),
  };
}

export const DELAYED_AFTER_MS = 15 * 60_000;

/** The "Data delayed" banner shows once updatedAt is more than 15 minutes old. */
export function isDelayed(updatedAt: string, now: number): boolean {
  return now - Date.parse(updatedAt) > DELAYED_AFTER_MS;
}

const POSTSEASON = ["Wild Card", "Divisional round", "Conference championships", "Pro Bowl week", "Super Bowl"];

/** "Week 12", or the postseason round's name. */
export function weekTitle(week: Pick<Week, "seasonType" | "number">): string {
  if (week.seasonType === "regular") return `Week ${week.number}`;
  return POSTSEASON[week.number - 1] ?? `Postseason week ${week.number}`;
}

export interface StatusText {
  kind: Game["status"];
  label: string;
}

/** Game Status as shown: the kickoff when Upcoming, quarter and clock when Live, "Final". */
export function statusText(game: Game, fmt: Pick<TimeFormat, "kickoff">): StatusText {
  if (game.status === "final") return { kind: "final", label: "Final" };
  if (game.status === "upcoming") return { kind: "upcoming", label: fmt.kickoff(game.kickoff) };
  const { period, clock } = game;
  if (period === null) return { kind: "live", label: "Live" };
  const q = period <= 4 ? `Q${period}` : period === 5 ? "OT" : `${period - 4}OT`;
  if (clock === "0:00" || clock === "00:00") return { kind: "live", label: period === 2 ? "Half" : `End ${q}` };
  return { kind: "live", label: clock ? `${q} ${clock}` : q };
}
