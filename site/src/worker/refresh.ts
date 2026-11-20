/**
 * The Worker's two cron runs (PLAN.md S10.1, research/leaderboard-live-spike.md "Full-week CPU"):
 *
 * - Scoreboard run: ESPN's scoreboard → KV `scoreboard` (the NFL Week and its Games).
 * - Stats run: KV `scoreboard` + Sleeper's weekly stats → KV `leaderboard`, which
 *   /api/leaderboard serves.
 *
 * ESPN's scoreboard and Sleeper's stats together don't fit the free plan's 10 ms of CPU, so each
 * run parses one response. fetch, KV and the clock are passed in, so tests use fakes.
 *
 * A failed run logs, returns `failed` and writes nothing: the last good data stays in KV.
 *
 * KV write budget (free plan: 1,000 writes a day). Each cron fires every 5 minutes, 288 times a day:
 * - `scoreboard` is written only when it changes. While a Game is Live its clock moves, so at worst
 *   288 a day; otherwise only at kickoffs and Finals.
 * - `leaderboard` is written when anything but updatedAt changes (so every run while a Game is Live),
 *   and otherwise once updatedAt is HEARTBEAT_MINUTES old, so "Updated … ago" on the page means
 *   the last successful refresh within that bound: 1,440 / 10 = 144 a day. At worst 288 a day.
 * - Worst case 288 + 288 = 576 a day; a quiet day 144 + a handful. That leaves over 400 a day for
 *   Sprint 11's Expected Points push (every 30 minutes on game days, 48 a day, plus kickoff windows).
 * KV reads: 1 per scoreboard run + 2 per stats run = 864 a day, against 100,000.
 */
import { KEYS, updatedAtOf, type KV } from "./kv.js";
import { buildLeaderboard, sameContent, serialize } from "./leaderboard.js";
import { parseScoreboard, type Scoreboard, type Week } from "./scoreboard.js";
import { sleeperStatsToPlayers } from "./sleeper.js";

export const SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard";
export const USER_AGENT = "ffp-leaderboard/0.1 (non-commercial; one request per source every 5 minutes)";
export { KEYS };
/** An unchanged Leaderboard is still rewritten, with a new updatedAt, once it is this old. */
export const HEARTBEAT_MINUTES = 10;
const DEFAULT_TIMEOUT_MS = 10_000;
/** As in the S9.1 recorder (scripts/record_live.py). K and DEF rows are skipped until Sprint 11. */
const SLEEPER_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"] as const;

export interface RunDeps {
  fetch: typeof fetch;
  kv: KV;
  now: () => Date;
  log: (message: string) => void;
  /** Per request; 10 s by default. */
  timeoutMs?: number;
}

export type Outcome = { result: "written" | "unchanged"; error?: undefined } | { result: "failed"; error: string };

/** Sleeper's weekly stats for the scoreboard's NFL Week. Postseason weeks are season_type=post. */
export function sleeperStatsUrl(week: Week): string {
  const type = week.seasonType === "postseason" ? "post" : "regular";
  const positions = SLEEPER_POSITIONS.map((p) => `&position[]=${p}`).join("");
  return `https://api.sleeper.com/stats/nfl/${week.season}/${week.number}?season_type=${type}${positions}`;
}

export async function runScoreboard(deps: RunDeps): Promise<Outcome> {
  return guarded("scoreboard run", deps, async () => {
    const text = JSON.stringify(parseScoreboard(await fetchJson(SCOREBOARD_URL, deps)));
    if ((await deps.kv.get(KEYS.scoreboard, { type: "text" })) === text) return "unchanged";
    await deps.kv.put(KEYS.scoreboard, text);
    return "written";
  });
}

export async function runStats(deps: RunDeps): Promise<Outcome> {
  return guarded("stats run", deps, async () => {
    const stored = await deps.kv.get(KEYS.scoreboard, { type: "text" });
    if (stored === null) throw new Error("no scoreboard in KV yet (the scoreboard run writes it)");
    const scoreboard = JSON.parse(stored) as Scoreboard;
    const rows = await fetchJson(sleeperStatsUrl(scoreboard.week), deps);
    if (!Array.isArray(rows)) throw new Error("Sleeper stats: expected a list of rows");
    const { players } = sleeperStatsToPlayers(rows);

    const now = deps.now();
    const text = serialize(buildLeaderboard(scoreboard, players, instant(now)));
    const previous = await deps.kv.get(KEYS.leaderboard, { type: "text" });
    if (previous !== null && sameContent(previous, text)) {
      const last = Date.parse(updatedAtOf(previous) ?? "");
      if (now.getTime() - last < HEARTBEAT_MINUTES * 60_000) return "unchanged";
    }
    await deps.kv.put(KEYS.leaderboard, text);
    return "written";
  });
}

async function guarded(what: string, deps: RunDeps, run: () => Promise<"written" | "unchanged">): Promise<Outcome> {
  try {
    return { result: await run() };
  } catch (e) {
    const error = e instanceof Error ? `${e.name}: ${e.message}` : String(e);
    deps.log(`${what} failed, keeping the last good data: ${error}`);
    return { result: "failed", error };
  }
}

async function fetchJson(url: string, deps: RunDeps): Promise<unknown> {
  const res = await deps.fetch(url, {
    headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
    signal: AbortSignal.timeout(deps.timeoutMs ?? DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

/** RFC 3339 in UTC without milliseconds, as the scoreboard's instants are. */
function instant(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}
