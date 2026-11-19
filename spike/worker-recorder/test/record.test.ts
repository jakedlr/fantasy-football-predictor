// The pure parts of the throwaway S9.1 Worker recorder: URLs, cheap extraction, manifest lines.
import { describe, expect, it } from "vitest";
import {
  boxscoreSlice,
  countPlays,
  inWindow,
  manifestRecord,
  scoreboardInfo,
  sha16,
  sourceUrls,
  toJsonl,
} from "../src/record";

const EVENT = "401873066";

function scoreboard(period = 2, clock = "7:41", state = "in", leaders: unknown = [{ v: 1 }]) {
  return JSON.stringify({
    events: [
      { id: "other", status: { period: 9, displayClock: "x", type: { state: "pre" } }, competitions: [{}] },
      {
        id: EVENT,
        status: { period, displayClock: clock, type: { state } },
        competitions: [
          {
            competitors: [
              { homeAway: "home", score: "7", team: { abbreviation: "HOU" } },
              { homeAway: "away", score: "3", team: { abbreviation: "IND" } },
            ],
            leaders,
            situation: { lastPlay: { id: "p9" } },
          },
        ],
      },
    ],
  });
}

describe("sourceUrls", () => {
  it("matches the Python recorder's three sources", () => {
    const urls = sourceUrls(EVENT, 2026, 11);
    expect(urls["espn-scoreboard"]).toBe("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard");
    expect(urls["espn-summary"]).toBe(
      `https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=${EVENT}`,
    );
    expect(urls["sleeper-stats"]).toBe(
      "https://api.sleeper.com/stats/nfl/2026/11?season_type=regular" +
        "&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF",
    );
  });
});

describe("scoreboardInfo", () => {
  it("reads the Game's state, period, clock, score and leaders", () => {
    const info = scoreboardInfo(scoreboard(), EVENT);
    expect(info).toMatchObject({ state: "in", period: 2, clock: "7:41", score: "IND 3 - HOU 7", last_play: "p9" });
    expect(info.statsText).toBe(JSON.stringify([{ v: 1 }]));
  });

  it("reports a missing event", () => {
    expect(scoreboardInfo(scoreboard(), "nope").error).toMatch(/not on the scoreboard/);
  });
});

describe("summary helpers", () => {
  const body = '{"boxscore":{"teams":[],"players":[{"a":1}]},"format":{},"drives":{"previous":[{"plays":[' +
    '{"id":"1","sequenceNumber":"1"},{"id":"2","sequenceNumber":"2"}]}],"current":{"plays":[{"sequenceNumber":"3"}]}}}';

  it("slices the box score out of the raw text without parsing it", () => {
    expect(boxscoreSlice(body)).toBe('{"teams":[],"players":[{"a":1}]}');
    expect(boxscoreSlice('{"nothing":1}')).toBeNull();
  });

  it("counts plays by their sequence numbers", () => {
    expect(countPlays(body)).toBe(3);
  });
});

describe("sha16", () => {
  it("is the first 16 hex characters of SHA-256", async () => {
    expect(await sha16("abc")).toBe("ba7816bf8f01cfea");
  });
});

describe("manifest lines", () => {
  it("have every field the Python manifest has, with vantage worker", () => {
    const rec = manifestRecord({ ts: new Date("2026-11-20T01:15:00.250Z"), source: "espn-summary", url: "u" });
    expect(Object.keys(rec)).toEqual([
      "ts", "vantage", "source", "url", "status", "latency_ms", "bytes",
      "state", "period", "clock", "score", "last_play", "plays", "rows", "last_modified", "stats_hash",
      "cache_control", "age", "cf_cache_status", "retry_after", "file", "error", "skipped",
    ]);
    expect(rec.ts).toBe("2026-11-20T01:15:00Z");
    expect(rec.vantage).toBe("worker");
    expect(rec.file).toBeNull();
  });

  it("join as one JSON object per line", () => {
    const a = manifestRecord({ ts: new Date(0), source: "espn-scoreboard", url: "a", status: 200 });
    const b = manifestRecord({ ts: new Date(0), source: "sleeper-stats", url: "b", status: 403 });
    const lines = toJsonl([a, b]).trimEnd().split("\n").map((l) => JSON.parse(l));
    expect(lines.map((l) => l.status)).toEqual([200, 403]);
    expect(toJsonl([a, b]).endsWith("\n")).toBe(true);
  });
});

describe("inWindow", () => {
  it("records only between RECORD_FROM and RECORD_UNTIL", () => {
    const env = { RECORD_FROM: "2026-11-20T00:45:00Z", RECORD_UNTIL: "2026-11-20T05:30:00Z" };
    expect(inWindow(new Date("2026-11-20T00:44:59Z"), env)).toBe(false);
    expect(inWindow(new Date("2026-11-20T01:15:00Z"), env)).toBe(true);
    expect(inWindow(new Date("2026-11-20T05:30:00Z"), env)).toBe(false);
  });
});
