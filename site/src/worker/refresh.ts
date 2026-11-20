/**
 * The Worker's three cron runs (PLAN.md S10.1 and S11.5, research/leaderboard-live-spike.md
 * "Full-week CPU" and "ESPN projections"):
 *
 * - Scoreboard run: ESPN's scoreboard → KV `scoreboard` (the NFL Week and its Games).
 * - Stats run: KV `scoreboard` + Sleeper's weekly stats → KV `leaderboard`, which
 *   /api/leaderboard serves.
 * - ESPN projections run: KV `scoreboard` + `idmap` + one position group of ESPN's weekly
 *   projections → KV `espn` (espn.ts), the fallback for players without `ffp` Expected Points.
 *
 * No two sources fit the free plan's 10 ms of CPU together, so each run parses one response; ESPN's
 * projections for all six positions (2.9 MB) don't fit either, so each run takes one group of
 * 0.7–0.9 MB. fetch, KV and the clock are passed in, so tests use fakes.
 *
 * A failed run logs, returns `failed` and writes nothing: the last good data stays in KV.
 *
 * KV write budget (free plan: 1,000 writes a day). The scoreboard and stats crons fire every
 * 5 minutes, 288 times a day; the ESPN cron 3 times an hour, 72 times a day:
 * - `scoreboard` is written only when it changes. While a Game is Live its clock moves, so at worst
 *   288 a day; otherwise only at kickoffs and Finals.
 * - `leaderboard` is written when anything but updatedAt changes (so every run while a Game is Live),
 *   and otherwise once updatedAt is HEARTBEAT_MINUTES old, so "Updated … ago" on the page means
 *   the last successful refresh within that bound: 1,440 / 10 = 144 a day. At worst 288 a day.
 * - `espn` is written only when a group's players changed: at worst 72 a day, never in the
 *   postseason.
 * - The Worker's worst case is 288 + 288 + 72 = 648 a day; a quiet day 144 + 72 + a handful.
 *   /api/expected-points adds about 48–100 a day (`expected` per push, `idmap` only when it
 *   changes), so at worst about 750 a day, 250 under the limit.
 * KV reads: 1 per scoreboard run + 2 per stats run + 3 per ESPN run = 1,080 a day, against 100,000.
 */
import { buildEspnStore, EspnProjectionsError, groupAt, instant as espnInstant, parseProjections, projectionsRequest } from "./espn.js";
import { KEYS, updatedAtOf, type EspnStore, type IdMapStore, type KV } from "./kv.js";
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

/**
 * ESPN projections run (S11.5): one position group per run (espn.ts `groupAt`), so each response
 * is under 0.9 MB. Joined through KV `idmap` and the scoreboard's Games, frozen at kickoff, and
 * written to KV `espn` only when the players changed. ESPN's fantasy game has no postseason weeks,
 * so in the postseason the run does nothing.
 */
export async function runEspn(deps: RunDeps): Promise<Outcome> {
  return guarded("ESPN projections run", deps, async () => {
    const scoreboardText = await deps.kv.get(KEYS.scoreboard, { type: "text" });
    if (scoreboardText === null) throw new Error("no scoreboard in KV yet (the scoreboard run writes it)");
    const scoreboard = JSON.parse(scoreboardText) as Scoreboard;
    if (scoreboard.week.seasonType !== "regular") return "unchanged";

    const now = deps.now();
    const group = groupAt(now);
    const { url, filter } = projectionsRequest(scoreboard.week, group);
    const projections = parseProjections(await fetchJson(url, deps, { "x-fantasy-filter": filter }), scoreboard.week, group.positions);

    const [idmapText, storedText] = await Promise.all([
      deps.kv.get(KEYS.idmap, { type: "text" }),
      deps.kv.get(KEYS.espn, { type: "text" }),
    ]);
    const idmap = idmapText === null ? null : readIdMap(idmapText);
    const stored = storedText === null ? null : readEspnStore(storedText, deps);
    const { store, counts } = buildEspnStore({ stored, scoreboard, projections, idmap, positions: group.positions, now });
    deps.log(
      `ESPN projections run (${group.positions.join(", ")}): ${counts.written} players, ${counts.unmapped} unmapped` +
        `${idmap === null ? " (no idmap yet)" : ""}, ${counts.noGame} not playing`,
    );

    if (JSON.stringify(store) === storedText) return "unchanged";
    store.fetchedAt = espnInstant(now);
    await deps.kv.put(KEYS.espn, JSON.stringify(store));
    return "written";
  });
}

/** KV `idmap`, written by /api/expected-points after the schema check; anything else is an error. */
function readIdMap(text: string): IdMapStore {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    value = null;
  }
  const map = (value as Partial<IdMapStore> | null)?.espnToSleeper;
  if (typeof map !== "object" || map === null || Array.isArray(map)) throw new EspnProjectionsError("KV idmap has no espnToSleeper map");
  return value as IdMapStore;
}

/** Our own KV `espn`; one we can't read is replaced rather than kept. */
function readEspnStore(text: string, deps: RunDeps): EspnStore | null {
  try {
    const value = JSON.parse(text) as EspnStore | null;
    if (value?.schemaVersion === 1 && typeof value.players === "object" && value.players !== null) return value;
  } catch {
    // replaced below
  }
  deps.log("ESPN projections run: replacing a KV espn it can't read");
  return null;
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

async function fetchJson(url: string, deps: RunDeps, headers: Record<string, string> = {}): Promise<unknown> {
  const res = await deps.fetch(url, {
    headers: { "User-Agent": USER_AGENT, Accept: "application/json", ...headers },
    signal: AbortSignal.timeout(deps.timeoutMs ?? DEFAULT_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

/** RFC 3339 in UTC without milliseconds, as the scoreboard's instants are. */
function instant(d: Date): string {
  return d.toISOString().replace(/\.\d{3}Z$/, "Z");
}
