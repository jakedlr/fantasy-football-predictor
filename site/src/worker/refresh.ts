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
 * - /api/expected-points (functions/api/expected-points.ts) writes `expected` once per accepted
 *   push and `idmap` only when it changed, at most once an hour. ffp pushes every 30 minutes on game
 *   days plus before each kickoff window: about 52 + a handful a day. The Function refuses a push
 *   under 5 minutes after the last (429), so whatever sends pushes, at most 288 + 24 = 312 a day.
 * - Worst case with ffp's schedule: 288 + 288 + 72 + ~60 ≈ 710 a day, against 1,000; a quiet day
 *   144 + 72 + a handful. A leaked push secret used at full rate could reach 648 + 312 = 960;
 *   rotating the secret (scripts/setup_cloudflare.sh, then a deploy) stops it.
 * KV reads: 1 per scoreboard run + 4 per stats run (`scoreboard`, `expected`, `espn`, `leaderboard`)
 * + 3 per ESPN run = 1,656 a day, plus 2 per push, against 100,000.
 *
 * The stats run reads KV `expected` and `espn` defensively: a store that isn't the shape kv.ts
 * describes is ignored with a log (the Leaderboard is built without it), and a malformed player
 * entry is dropped with a log; neither fails the run.
 */
import { buildEspnStore, EspnProjectionsError, groupAt, instant as espnInstant, parseProjections, projectionsRequest } from "./espn.js";
import { KEYS, updatedAtOf, type EspnStore, type ExpectedStore, type IdMapStore, type KV } from "./kv.js";
import { buildLeaderboard, sameContent, serialize } from "./leaderboard.js";
import { parseScoreboard, type Scoreboard, type Week } from "./scoreboard.js";
import { sleeperStatsToPlayers } from "./sleeper.js";

export const SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard";
export const USER_AGENT = "ffp-leaderboard/0.1 (non-commercial; one request per source every 5 minutes)";
export { KEYS };
/** An unchanged Leaderboard is still rewritten, with a new updatedAt, once it is this old. */
export const HEARTBEAT_MINUTES = 10;
const DEFAULT_TIMEOUT_MS = 10_000;
/** As in the S9.1 recorder (scripts/record_live.py). K and DEF rows become kicker and D/ST lines (S11.1). */
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
    const [rows, expectedText, espnText] = await Promise.all([
      fetchJson(sleeperStatsUrl(scoreboard.week), deps),
      deps.kv.get(KEYS.expected, { type: "text" }),
      deps.kv.get(KEYS.espn, { type: "text" }),
    ]);
    if (!Array.isArray(rows)) throw new Error("Sleeper stats: expected a list of rows");
    const { players } = sleeperStatsToPlayers(rows);
    const expected = readStore<ExpectedStore>(KEYS.expected, expectedText, "generatedAt", deps.log);
    const espn = readStore<EspnStore>(KEYS.espn, espnText, "fetchedAt", deps.log);

    const now = deps.now();
    const text = serialize(buildLeaderboard(scoreboard, players, instant(now), { expected, espn }));
    const previous = await deps.kv.get(KEYS.leaderboard, { type: "text" });
    if (previous !== null && sameContent(previous, text)) {
      const last = Date.parse(updatedAtOf(previous) ?? "");
      if (now.getTime() - last < HEARTBEAT_MINUTES * 60_000) return "unchanged";
    }
    await deps.kv.put(KEYS.leaderboard, text);
    return "written";
  });
}

const POSITIONS: ReadonlySet<string> = new Set(["QB", "RB", "WR", "TE", "K", "D/ST"]);
const PLAYER_ID = /^(sleeper:\d+|dst:[A-Z]{2,3})$/;
const TEAM = /^[A-Z]{2,3}$/;

const isObject = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);

function numbersOnly(x: unknown): boolean {
  if (!isObject(x)) return false;
  for (const k in x) if (typeof x[k] !== "number" || !Number.isFinite(x[k])) return false;
  return true;
}

/** Whether one `players` entry is an `Expectation` the Leaderboard can use. */
function usableEntry(id: string, e: unknown): boolean {
  return PLAYER_ID.test(id) && isObject(e)
    && typeof e.name === "string" && e.name !== ""
    && typeof e.team === "string" && TEAM.test(e.team)
    && typeof e.position === "string" && POSITIONS.has(e.position)
    && numbersOnly(e.statMeans) && numbersOnly(e.expected);
}

/**
 * The last store read per KV key, kept for as long as Cloudflare keeps this isolate. The stores
 * change every 30 minutes at most, the stats run every 5, and parsing both is a quarter or more of
 * the run's CPU on a full Sunday (test/worker/cpu.bench.test.ts); comparing the text is far cheaper.
 * Never mutated after it is cached (buildLeaderboard only reads).
 */
const storeCache = new Map<string, { text: string; store: unknown; logs: string[] }>();

/**
 * KV `expected` or `espn` → the store, or null when it's missing or not the shape kv.ts describes
 * (logged). Malformed player entries are dropped (one log line). Never throws. An unchanged text
 * returns the store parsed before, and logs what it logged then.
 */
export function readStore<S extends ExpectedStore | EspnStore>(key: string, text: string | null, stamp: "generatedAt" | "fetchedAt", log: (m: string) => void): S | null {
  if (text === null) return null;
  let hit = storeCache.get(key);
  if (hit?.text !== text) {
    const logs: string[] = [];
    hit = { text, store: parseStore(key, text, stamp, logs), logs };
    storeCache.set(key, hit);
  }
  for (const m of hit.logs) log(m);
  return hit.store as S | null;
}

function parseStore(key: string, text: string, stamp: "generatedAt" | "fetchedAt", logs: string[]): unknown {
  let store: unknown;
  try {
    store = JSON.parse(text);
  } catch {
    logs.push(`KV ${key} isn't JSON; ignored`);
    return null;
  }
  if (!isObject(store) || store.schemaVersion !== 1 || !Number.isInteger(store.season) || !Number.isInteger(store.week)
    || (store.seasonType !== "regular" && store.seasonType !== "postseason") || typeof store[stamp] !== "string" || !isObject(store.players)) {
    logs.push(`KV ${key} isn't the store kv.ts describes; ignored`);
    return null;
  }
  const players = store.players;
  let bad = 0;
  for (const id in players) {
    if (!usableEntry(id, players[id])) {
      delete players[id];
      bad++;
    }
  }
  if (bad > 0) logs.push(`KV ${key}: ${bad} malformed player entr${bad === 1 ? "y" : "ies"} ignored`);
  return store;
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
