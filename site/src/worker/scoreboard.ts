/**
 * ESPN's NFL scoreboard → the NFL Week and its Games, shaped as `week` and `game` in
 * site/schema/leaderboard.schema.json. Pure: no fetch, no clock.
 *
 * Source: https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard
 * Fixtures: site/test/fixtures/espn/scoreboard-*.json
 *
 * Runs inside the free Worker's 10 ms of CPU, so it reads each event once and copies only
 * the few fields it keeps. A response of the wrong shape throws a ScoreboardError; the caller
 * keeps the last good data (PLAN.md, "Live stats").
 */

export type GameStatus = "upcoming" | "live" | "final";

export interface Week {
  season: number;
  seasonType: "regular" | "postseason";
  /**
   * The week within the season type. Postseason weeks are numbered by round, 1 (Wild Card) to 4
   * (Super Bowl), as ffp numbers them: ESPN's calendar numbers the Super Bowl 5 in a season with a
   * Pro Bowl week (2025; that week itself is refused, below) and gives it one two-week entry, 4, in
   * a season without one (2026), so ESPN's "Super Bowl" entry becomes 4.
   */
  number: number;
  start: string;
  end: string;
  firstKickoff: string;
  lastKickoff: string;
}

export interface Game {
  /** ESPN event id. */
  id: string;
  kickoff: string;
  status: GameStatus;
  /** Sleeper's team code. */
  home: string;
  away: string;
  homeScore: number | null;
  awayScore: number | null;
  /** Quarter; 5+ is overtime. null unless Live. */
  period: number | null;
  /** Game clock, m:ss. null unless Live. */
  clock: string | null;
}

export interface Scoreboard {
  week: Week;
  games: Game[];
}

/**
 * `shape`: the response isn't the scoreboard we know (or names a team we don't know).
 * `out-of-season`: a well-formed scoreboard for a week the Leaderboard doesn't cover
 * (preseason, off-season, the Pro Bowl).
 */
export class ScoreboardError extends Error {
  readonly kind: "shape" | "out-of-season";
  constructor(message: string, kind: "shape" | "out-of-season" = "shape") {
    super(`ESPN scoreboard: ${message}`);
    this.name = "ScoreboardError";
    this.kind = kind;
  }
}

/**
 * ESPN abbreviation → Sleeper team code. The Worker joins Sleeper's stat rows to Games by
 * team, so every Game carries Sleeper's codes. Checked 2026-11-20 against ESPN's /teams and
 * the `team` field of Sleeper's 2026 Week 9 and 10 stats: only Washington differs.
 */
export const ESPN_TO_SLEEPER_TEAM: Readonly<Record<string, string>> = Object.freeze({
  ARI: "ARI", ATL: "ATL", BAL: "BAL", BUF: "BUF", CAR: "CAR", CHI: "CHI", CIN: "CIN", CLE: "CLE",
  DAL: "DAL", DEN: "DEN", DET: "DET", GB: "GB", HOU: "HOU", IND: "IND", JAX: "JAX", KC: "KC",
  LAC: "LAC", LAR: "LAR", LV: "LV", MIA: "MIA", MIN: "MIN", NE: "NE", NO: "NO", NYG: "NYG",
  NYJ: "NYJ", PHI: "PHI", PIT: "PIT", SEA: "SEA", SF: "SF", TB: "TB", TEN: "TEN", WSH: "WAS",
});

/** Sleeper's code for an ESPN abbreviation; throws on one we don't know rather than guess. */
export function sleeperTeam(espn: string): string {
  const code = Object.hasOwn(ESPN_TO_SLEEPER_TEAM, espn) ? ESPN_TO_SLEEPER_TEAM[espn] : undefined;
  if (code === undefined) throw new ScoreboardError(`unknown team abbreviation ${JSON.stringify(espn)}`);
  return code;
}

const SEASON_TYPES: Readonly<Record<number, Week["seasonType"]>> = { 2: "regular", 3: "postseason" };
const CLOCK = /^\d{1,2}:\d{2}$/;
const SCORE = /^\d+$/;

/** The Super Bowl's postseason round. */
const SUPER_BOWL_ROUND = 4;

export function parseScoreboard(json: unknown): Scoreboard {
  const root = object(json, "response");
  const seasonObj = object(root.season, "season");
  const season = integer(seasonObj.year, "season.year");
  const typeId = integer(seasonObj.type, "season.type");
  const number = integer(object(root.week, "week").number, "week.number");
  const seasonType = SEASON_TYPES[typeId];
  if (seasonType === undefined) {
    throw new ScoreboardError(`season type ${typeId} is neither regular season (2) nor postseason (3)`, "out-of-season");
  }
  const { start, end, label } = calendarWeek(root.leagues, typeId, number);
  if (/pro bowl/i.test(label)) throw new ScoreboardError(`week ${number} is the Pro Bowl`, "out-of-season");
  const round = seasonType === "postseason" && /super bowl/i.test(label) ? SUPER_BOWL_ROUND : number;

  const events = array(root.events, "events");
  const games: Game[] = [];
  let firstKickoff: string | undefined;
  let lastKickoff: string | undefined;
  for (let i = 0; i < events.length; i++) {
    const game = parseEvent(events[i], `events[${i}]`);
    if (game === null) continue;
    games.push(game);
    // ISO strings in one format sort as text.
    if (firstKickoff === undefined || game.kickoff < firstKickoff) firstKickoff = game.kickoff;
    if (lastKickoff === undefined || game.kickoff > lastKickoff) lastKickoff = game.kickoff;
  }
  if (firstKickoff === undefined || lastKickoff === undefined) throw new ScoreboardError("no Games this week");

  return { week: { season, seasonType, number: round, start, end, firstKickoff, lastKickoff }, games };
}

/** ESPN's start and end for this week, from leagues[0].calendar (the season type's entries). */
function calendarWeek(leagues: unknown, typeId: number, number: number): { start: string; end: string; label: string } {
  const calendar = array(object(array(leagues, "leagues")[0], "leagues[0]").calendar, "leagues[0].calendar");
  const section = calendar.find((s) => isObject(s) && s.value === String(typeId));
  const entries = isObject(section) && Array.isArray(section.entries) ? section.entries : [];
  const entry = entries.find((e) => isObject(e) && e.value === String(number));
  if (!isObject(entry)) throw new ScoreboardError(`no calendar entry for season type ${typeId}, week ${number}`);
  return {
    start: instant(entry.startDate, "calendar week startDate"),
    end: instant(entry.endDate, "calendar week endDate"),
    label: typeof entry.label === "string" ? entry.label : "",
  };
}

/**
 * One event → one Game, or null for a Game we leave out.
 *
 * Game Status follows `status.type.state`: pre → upcoming, in → live, post → final, with
 * these deliberate exceptions, keyed on `status.type.name`:
 * - STATUS_POSTPONED (state "post", not completed) → upcoming, no scores. It hasn't been
 *   played; calling it Final would rank its players at 0. ESPN moves its date when it's
 *   rescheduled, and its players keep their Expected Points meanwhile.
 * - STATUS_CANCELED (state "post", not completed) → left out, as if the teams were on bye: it
 *   won't be played, so its players score nothing this NFL Week. It doesn't count towards
 *   firstKickoff or lastKickoff either.
 * - STATUS_DELAYED needs no exception: ESPN gives it state "pre" before kickoff (→ upcoming)
 *   and "in" once play has started (→ live, clock frozen where it stopped).
 * Halftime (STATUS_HALFTIME) and the end of a quarter (STATUS_END_PERIOD) are state "in", so
 * Live, with ESPN's clock of 0:00.
 */
function parseEvent(raw: unknown, at: string): Game | null {
  const event = object(raw, at);
  const id = string(event.id, `${at}.id`);
  const kickoff = instant(event.date, `${at}.date`);
  const st = object(event.status, `${at}.status`);
  const type = object(st.type, `${at}.status.type`);
  const name = typeof type.name === "string" ? type.name : "";
  if (name === "STATUS_CANCELED") return null;

  let status: GameStatus;
  if (name === "STATUS_POSTPONED") status = "upcoming";
  else if (type.state === "pre") status = "upcoming";
  else if (type.state === "in") status = "live";
  else if (type.state === "post") status = "final";
  else throw new ScoreboardError(`${at}.status.type.state is ${JSON.stringify(type.state)}, not pre, in or post`);

  const competitors = array(object(array(event.competitions, `${at}.competitions`)[0], `${at}.competitions[0]`).competitors, `${at}.competitions[0].competitors`);
  let home: Record<string, unknown> | undefined;
  let away: Record<string, unknown> | undefined;
  for (const c of competitors) {
    if (!isObject(c)) continue;
    if (c.homeAway === "home") home = c;
    else if (c.homeAway === "away") away = c;
  }
  const where = `${at}.competitions[0].competitors`;
  if (home === undefined || away === undefined) throw new ScoreboardError(`${where} lacks a home and an away team`);

  const scored = status !== "upcoming";
  const live = status === "live";
  return {
    id,
    kickoff,
    status,
    home: sleeperTeam(string(object(home.team, `${where} home.team`).abbreviation, `${where} home.team.abbreviation`)),
    away: sleeperTeam(string(object(away.team, `${where} away.team`).abbreviation, `${where} away.team.abbreviation`)),
    homeScore: scored ? score(home.score, `${where} home.score`) : null,
    awayScore: scored ? score(away.score, `${where} away.score`) : null,
    // ESPN can report period 0 in the instant a Game goes Live; that is the first quarter.
    period: live ? Math.max(1, integer(st.period, `${at}.status.period`)) : null,
    clock: live ? clock(st, `${at}.status`) : null,
  };
}

/** ESPN's displayClock when it's m:ss; otherwise rebuilt from `clock`, the seconds left. */
function clock(st: Record<string, unknown>, at: string): string {
  if (typeof st.displayClock === "string" && CLOCK.test(st.displayClock)) return st.displayClock;
  const seconds = st.clock;
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0 || seconds >= 6000) {
    throw new ScoreboardError(`${at} has no readable clock (displayClock ${JSON.stringify(st.displayClock)}, clock ${String(seconds)})`);
  }
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

function score(value: unknown, at: string): number {
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) return value;
  if (typeof value === "string" && SCORE.test(value)) return Number(value);
  throw new ScoreboardError(`${at} is ${JSON.stringify(value)}, not a whole-number score`);
}

/** ESPN writes "2026-11-20T01:15Z"; the schema wants RFC 3339 with seconds, in UTC. */
function instant(value: unknown, at: string): string {
  const ms = typeof value === "string" ? Date.parse(value) : Number.NaN;
  if (Number.isNaN(ms)) throw new ScoreboardError(`${at} is ${JSON.stringify(value)}, not a date-time`);
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function object(value: unknown, at: string): Record<string, unknown> {
  if (!isObject(value)) throw new ScoreboardError(`${at} is missing or not an object`);
  return value;
}

function array(value: unknown, at: string): unknown[] {
  if (!Array.isArray(value)) throw new ScoreboardError(`${at} is missing or not an array`);
  return value;
}

function string(value: unknown, at: string): string {
  if (typeof value !== "string" || value === "") throw new ScoreboardError(`${at} is missing or not a string`);
  return value;
}

function integer(value: unknown, at: string): number {
  if (typeof value !== "number" || !Number.isInteger(value)) throw new ScoreboardError(`${at} is missing or not an integer`);
  return value;
}
