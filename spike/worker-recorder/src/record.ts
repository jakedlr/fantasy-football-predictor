// Pure helpers for the throwaway S9.1 Worker recorder. The manifest format matches
// scripts/record_live.py so scripts/analyze_live_spike.py reads both.
//
// CPU budget (free plan, 10 ms per run): only the scoreboard (about 240 KB) is parsed.
// The summary (about 600 KB) is never parsed: its box score is sliced out of the raw
// text and hashed, and plays are counted by their "sequenceNumber" keys. Sleeper's raw
// text is hashed whole. So stats hashes are not comparable across vantage points; the
// analyzer only compares a hash with the previous one in the same stream.

export const SOURCES = ["espn-scoreboard", "espn-summary", "sleeper-stats"] as const;
export type Source = (typeof SOURCES)[number];

const ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl";
const SLEEPER_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"];
export const USER_AGENT =
  "ffp-live-spike/0.1 (non-commercial fantasy research; one request per source per minute)";

export function sourceUrls(eventId: string, season: number, week: number): Record<Source, string> {
  const positions = SLEEPER_POSITIONS.map((p) => `&position[]=${p}`).join("");
  return {
    "espn-scoreboard": `${ESPN}/scoreboard`,
    "espn-summary": `${ESPN}/summary?event=${eventId}`,
    "sleeper-stats": `https://api.sleeper.com/stats/nfl/${season}/${week}?season_type=regular${positions}`,
  };
}

export interface ScoreboardInfo {
  state?: string | null;
  period?: number | null;
  clock?: string | null;
  score?: string | null;
  last_play?: string | null;
  statsText?: string;
  error?: string;
}

type Json = any;

export function scoreboardInfo(text: string, eventId: string): ScoreboardInfo {
  let data: Json;
  try {
    data = JSON.parse(text);
  } catch (e) {
    return { error: `parse: ${String(e)}`.slice(0, 300) };
  }
  const event = (data.events ?? []).find((e: Json) => e.id === eventId);
  if (!event) return { error: `event ${eventId} not on the scoreboard` };
  const comp = event.competitions?.[0] ?? {};
  const byside: Record<string, Json> = {};
  for (const c of comp.competitors ?? []) byside[c.homeAway] = c;
  const { home, away } = byside;
  return {
    state: event.status?.type?.state ?? null,
    period: event.status?.period ?? null,
    clock: event.status?.displayClock ?? null,
    score: home && away
      ? `${away.team.abbreviation} ${away.score} - ${home.team.abbreviation} ${home.score}`
      : null,
    last_play: comp.situation?.lastPlay?.id ?? null,
    statsText: JSON.stringify(comp.leaders ?? []),
  };
}

const BOX_START = '"boxscore":';
const BOX_END = ',"format":';

/** The summary's `boxscore` object as raw text (ESPN puts it first, followed by `format`). */
export function boxscoreSlice(text: string): string | null {
  const start = text.indexOf(BOX_START);
  if (start < 0) return null;
  const end = text.indexOf(BOX_END, start);
  return end < 0 ? null : text.slice(start + BOX_START.length, end);
}

export function countPlays(text: string): number {
  let n = 0;
  for (let i = text.indexOf('"sequenceNumber":'); i >= 0; i = text.indexOf('"sequenceNumber":', i + 1)) n++;
  return n;
}

export async function sha16(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].slice(0, 8).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export interface ManifestRecord {
  ts: string;
  vantage: string;
  source: Source;
  url: string;
  status: number | null;
  latency_ms: number | null;
  bytes: number | null;
  state: string | null;
  period: number | null;
  clock: string | null;
  score: string | null;
  last_play: string | null;
  plays: number | null;
  rows: number | null;
  last_modified: number | null;
  stats_hash: string | null;
  cache_control: string | null;
  age: string | null;
  cf_cache_status: string | null;
  retry_after: string | null;
  file: null;
  error: string | null;
  skipped: string | null;
}

export function isoSeconds(t: Date): string {
  return t.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function manifestRecord(
  fields: Partial<Omit<ManifestRecord, "ts">> & { ts: Date; source: Source; url: string },
): ManifestRecord {
  const { ts, source, url, ...rest } = fields;
  return {
    ts: isoSeconds(ts), vantage: "worker", source, url, status: null, latency_ms: null, bytes: null,
    state: null, period: null, clock: null, score: null, last_play: null, plays: null, rows: null,
    last_modified: null, stats_hash: null, cache_control: null, age: null, cf_cache_status: null,
    retry_after: null, file: null, error: null, skipped: null,
    ...rest,
  };
}

export function toJsonl(records: ManifestRecord[]): string {
  return records.map((r) => JSON.stringify(r) + "\n").join("");
}

export function inWindow(now: Date, env: { RECORD_FROM: string; RECORD_UNTIL: string }): boolean {
  return now >= new Date(env.RECORD_FROM) && now < new Date(env.RECORD_UNTIL);
}
