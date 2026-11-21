import { readFileSync } from "node:fs";
import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { ESPN_TO_SLEEPER_TEAM, ScoreboardError, parseScoreboard, sleeperTeam } from "../../src/worker/scoreboard.js";

// Fixtures are read fresh on every call, so a test can mutate its copy freely.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Json = any;
const read = (path: string): Json => JSON.parse(readFileSync(new URL(`../${path}`, import.meta.url), "utf8"));
const fixture = (name: string): Json => read(`fixtures/espn/scoreboard-${name}.json`);

const schema = read("../schema/leaderboard.schema.json");
const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats.default(ajv);
ajv.addSchema(schema);
const validateWeek = ajv.compile({ $ref: `${schema.$id}#/properties/week` });
const validateGame = ajv.compile({ $ref: `${schema.$id}#/$defs/game` });

/** The first event of the upcoming fixture (IND @ HOU), restyled with the given status. */
function withStatus(status: Json, scores: [string, string] = ["14", "7"]): Json {
  const board = fixture("upcoming");
  const event = board.events[0];
  event.status = status;
  event.competitions[0].competitors[0].score = scores[0];
  event.competitions[0].competitors[1].score = scores[1];
  return board;
}
const status = (name: string, state: string, period: number, displayClock: string, clock = 0, completed = false) => ({
  clock,
  displayClock,
  period,
  type: { name, state, completed },
});

// Sleeper's 32 team codes, from its 2026 Week 9 and 10 stats (`team` field), 2026-11-20.
const SLEEPER_TEAMS = [
  "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
  "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
];
// ESPN's 32 abbreviations, from its /teams endpoint, 2026-11-20.
const ESPN_TEAMS = [
  "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
  "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WSH",
];

describe("parseScoreboard: the NFL Week", () => {
  it("takes the week number, season and ESPN's week start and end from the calendar", () => {
    const { week } = parseScoreboard(fixture("upcoming"));
    expect(week).toEqual({
      season: 2026,
      seasonType: "regular",
      number: 11,
      start: "2026-11-18T08:00:00Z",
      end: "2026-11-25T07:59:00Z",
      firstKickoff: "2026-11-20T01:15:00Z",
      lastKickoff: "2026-11-24T01:15:00Z",
    });
  });

  it("reads a postseason week", () => {
    const { week, games } = parseScoreboard(fixture("postseason"));
    expect(week).toEqual({
      season: 2025,
      seasonType: "postseason",
      number: 1,
      start: "2026-01-07T08:00:00Z",
      end: "2026-01-14T07:59:00Z",
      firstKickoff: "2026-01-10T21:30:00Z",
      lastKickoff: "2026-01-13T01:15:00Z",
    });
    expect(games).toHaveLength(6);
  });

  it("refuses a preseason or off-season scoreboard, which the Leaderboard doesn't cover", () => {
    for (const type of [1, 4]) {
      const board = fixture("upcoming");
      board.season.type = type;
      expect(() => parseScoreboard(board)).toThrow(ScoreboardError);
      try {
        parseScoreboard(board);
      } catch (e) {
        expect((e as ScoreboardError).kind).toBe("out-of-season");
      }
    }
  });

  it("refuses the Pro Bowl week, whose AFC and NFC teams aren't fantasy teams", () => {
    const board = fixture("postseason");
    board.week.number = 4;
    expect(() => parseScoreboard(board)).toThrow(/Pro Bowl/);
  });

  it("reads a Super Bowl week 4 in a season with no Pro Bowl week (ESPN's 2026 calendar)", () => {
    const board = fixture("postseason");
    const post = board.leagues[0].calendar.find((s: { value: string }) => s.value === "3");
    post.entries = post.entries
      .filter((e: { label: string }) => e.label !== "Pro Bowl")
      .map((e: { label: string; value: string }) => (e.label === "Super Bowl" ? { ...e, value: "4" } : e));
    board.week.number = 4;
    const { week } = parseScoreboard(board);
    expect(week.seasonType).toBe("postseason");
    expect(week.number).toBe(4);
  });

  it("numbers postseason weeks by round: ESPN's Super Bowl week 5 (after a Pro Bowl week) is 4", () => {
    const board = fixture("postseason");
    board.week.number = 5;
    const { week } = parseScoreboard(board);
    expect(week.seasonType).toBe("postseason");
    expect(week.number).toBe(4);
  });

  it("refuses a week missing from ESPN's calendar", () => {
    const board = fixture("upcoming");
    board.week.number = 30;
    expect(() => parseScoreboard(board)).toThrow(/calendar/);
  });
});

describe("parseScoreboard: Games and Game Status", () => {
  it("maps a whole upcoming week: no scores, period or clock", () => {
    const { games } = parseScoreboard(fixture("upcoming"));
    expect(games).toHaveLength(13);
    expect(games[0]).toEqual({
      id: "401873066",
      kickoff: "2026-11-20T01:15:00Z",
      status: "upcoming",
      home: "HOU",
      away: "IND",
      homeScore: null,
      awayScore: null,
      period: null,
      clock: null,
    });
    expect(games.every((g) => g.status === "upcoming" && g.homeScore === null && g.period === null)).toBe(true);
  });

  it("maps a Live Game with its quarter, clock and score", () => {
    const { games } = parseScoreboard(fixture("live"));
    expect(games[0]).toMatchObject({ status: "live", homeScore: 17, awayScore: 3, period: 3, clock: "14:12" });
    expect(games.slice(1).every((g) => g.status === "upcoming")).toBe(true);
  });

  it("maps a Final Game with its score and no period or clock", () => {
    const { games } = parseScoreboard(fixture("final"));
    expect(games[0]).toMatchObject({ status: "final", homeScore: 26, awayScore: 3, period: null, clock: null });
  });

  it("maps a full week with byes: every Game Final, bye teams absent", () => {
    const { week, games } = parseScoreboard(fixture("week10-byes"));
    expect(week.number).toBe(10);
    expect(games).toHaveLength(14);
    expect(games.every((g) => g.status === "final" && g.homeScore !== null && g.awayScore !== null)).toBe(true);
    const teams = new Set(games.flatMap((g) => [g.home, g.away]));
    expect(teams.size).toBe(28);
    for (const bye of ["CHI", "DEN", "PHI", "TB"]) expect(teams.has(bye)).toBe(false);
    expect(games.find((g) => g.id === "401873052")).toMatchObject({ home: "NYG", away: "WAS", homeScore: 21, awayScore: 24 });
  });

  it("keeps halftime Live, with a valid clock", () => {
    const { games } = parseScoreboard(withStatus(status("STATUS_HALFTIME", "in", 2, "0:00")));
    expect(games[0]).toMatchObject({ status: "live", period: 2, clock: "0:00" });
  });

  it("keeps the end of a quarter Live, with a valid clock", () => {
    const { games } = parseScoreboard(withStatus(status("STATUS_END_PERIOD", "in", 1, "0:00")));
    expect(games[0]).toMatchObject({ status: "live", period: 1, clock: "0:00" });
  });

  it("reads overtime as period 5", () => {
    const { games } = parseScoreboard(withStatus(status("STATUS_IN_PROGRESS", "in", 5, "8:41", 521)));
    expect(games[0]).toMatchObject({ status: "live", period: 5, clock: "8:41" });
  });

  it("rebuilds the clock from seconds when ESPN's display clock isn't m:ss", () => {
    const { games } = parseScoreboard(withStatus(status("STATUS_IN_PROGRESS", "in", 4, "45.3", 45.3)));
    expect(games[0]).toMatchObject({ clock: "0:45" });
  });

  it("reads a Live Game ESPN hasn't given a quarter yet as the first quarter", () => {
    const { games } = parseScoreboard(withStatus(status("STATUS_IN_PROGRESS", "in", 0, "15:00", 900)));
    expect(games[0]).toMatchObject({ status: "live", period: 1, clock: "15:00" });
  });

  it("keeps a Game delayed mid-play Live, clock frozen", () => {
    const { games } = parseScoreboard(withStatus(status("STATUS_DELAYED", "in", 3, "6:02", 362)));
    expect(games[0]).toMatchObject({ status: "live", period: 3, clock: "6:02", homeScore: 14, awayScore: 7 });
  });

  it("keeps a Game delayed before kickoff Upcoming", () => {
    const { games } = parseScoreboard(withStatus(status("STATUS_DELAYED", "pre", 0, "0:00"), ["0", "0"]));
    expect(games[0]).toMatchObject({ status: "upcoming", homeScore: null, period: null, clock: null });
  });

  it("keeps a postponed Game Upcoming, not Final, with no scores", () => {
    const { games, week } = parseScoreboard(withStatus(status("STATUS_POSTPONED", "post", 0, "0:00"), ["0", "0"]));
    expect(games).toHaveLength(13);
    expect(games[0]).toMatchObject({ status: "upcoming", homeScore: null, awayScore: null, period: null, clock: null });
    expect(week.firstKickoff).toBe("2026-11-20T01:15:00Z");
  });

  it("drops a canceled Game, from the Games and from the kickoff range", () => {
    const { games, week } = parseScoreboard(withStatus(status("STATUS_CANCELED", "post", 0, "0:00"), ["0", "0"]));
    expect(games).toHaveLength(12);
    expect(games.some((g) => g.id === "401873066")).toBe(false);
    expect(week.firstKickoff).toBe("2026-11-22T18:00:00Z");
  });

  it("fits the schema's week and game for every fixture and status", () => {
    const boards = ["upcoming", "live", "final", "week10-byes", "postseason"].map(fixture);
    boards.push(
      withStatus(status("STATUS_HALFTIME", "in", 2, "0:00")),
      withStatus(status("STATUS_END_PERIOD", "in", 3, "0:00")),
      withStatus(status("STATUS_IN_PROGRESS", "in", 4, "45.3", 45.3)),
      withStatus(status("STATUS_POSTPONED", "post", 0, "0:00")),
    );
    for (const board of boards) {
      const { week, games } = parseScoreboard(board);
      expect(validateWeek(week), JSON.stringify(validateWeek.errors)).toBe(true);
      for (const game of games) expect(validateGame(game), JSON.stringify(validateGame.errors)).toBe(true);
    }
  });
});

describe("team codes", () => {
  it("maps all 32 ESPN abbreviations onto Sleeper's 32 codes", () => {
    expect(Object.keys(ESPN_TO_SLEEPER_TEAM).sort()).toEqual([...ESPN_TEAMS].sort());
    expect(Object.values(ESPN_TO_SLEEPER_TEAM).sort()).toEqual([...SLEEPER_TEAMS].sort());
  });

  it("differs only for Washington", () => {
    const differing = Object.entries(ESPN_TO_SLEEPER_TEAM).filter(([espn, sleeper]) => espn !== sleeper);
    expect(differing).toEqual([["WSH", "WAS"]]);
    expect(sleeperTeam("WSH")).toBe("WAS");
    expect(sleeperTeam("KC")).toBe("KC");
  });

  it("refuses a team it doesn't know rather than guess", () => {
    expect(() => sleeperTeam("XYZ")).toThrow(ScoreboardError);
    const board = fixture("upcoming");
    board.events[0].competitions[0].competitors[0].team.abbreviation = "OAK";
    expect(() => parseScoreboard(board)).toThrow(/OAK/);
  });
});

describe("parseScoreboard: a response of the wrong shape", () => {
  const broken: Array<[string, (board: Json) => Json]> = [
    ["null", () => null],
    ["an HTML error page", () => "<html>403</html>"],
    ["no events", (b) => ({ ...b, events: undefined })],
    ["an empty week", (b) => ({ ...b, events: [] })],
    ["no season", (b) => ({ ...b, season: undefined })],
    ["no calendar", (b) => ({ ...b, leagues: [] })],
    ["an event without an id", (b) => (delete b.events[0].id, b)],
    ["a bad kickoff time", (b) => ((b.events[0].date = "tomorrow"), b)],
    ["no competitors", (b) => ((b.events[0].competitions = []), b)],
    ["no home team", (b) => ((b.events[0].competitions[0].competitors[0].homeAway = "away"), b)],
    ["no status", (b) => (delete b.events[0].status, b)],
    ["an unknown status state", (b) => ((b.events[0].status.type.state = "paused"), b)],
  ];
  it.each(broken)("throws a ScoreboardError on %s", (_, breakIt) => {
    expect(() => parseScoreboard(breakIt(fixture("upcoming")))).toThrow(ScoreboardError);
  });

  it("throws on a Live score that isn't a whole number", () => {
    const board = fixture("live");
    board.events[0].competitions[0].competitors[0].score = "seventeen";
    expect(() => parseScoreboard(board)).toThrow(/score/);
  });

  it("throws on a Live Game whose clock can't be read", () => {
    expect(() => parseScoreboard(withStatus(status("STATUS_IN_PROGRESS", "in", 2, "--", Number.NaN)))).toThrow(/clock/);
  });

  it("names the event and field in the message, and marks the error as a shape error", () => {
    const board = fixture("upcoming");
    delete board.events[2].id;
    try {
      parseScoreboard(board);
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(ScoreboardError);
      expect((e as ScoreboardError).kind).toBe("shape");
      expect((e as Error).message).toMatch(/events\[2\]\.id/);
    }
  });
});
