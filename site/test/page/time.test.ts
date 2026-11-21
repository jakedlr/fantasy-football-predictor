import { describe, expect, it } from "vitest";
import { DELAYED_AFTER_MS, isDelayed, makeTimeFormat, statusText, weekTitle } from "../../src/page/time.js";
import type { Game } from "../../src/page/types.js";

// ICU puts narrow no-break and thin spaces in times and ranges; compare with plain spaces.
const plain = (s: string) => s.replace(/[\u202f\u00a0\u2009]/g, " ");
const ny = makeTimeFormat({ locale: "en-US", timeZone: "America/New_York" });
const london = makeTimeFormat({ locale: "en-GB", timeZone: "Europe/London" });
const updatedAt = "2026-11-22T18:05:00Z";
const t = Date.parse(updatedAt);

const game = (over: Partial<Game>): Game => ({
  id: "1", kickoff: "2026-11-22T18:00:00Z", status: "upcoming", home: "BAL", away: "CIN",
  homeScore: null, awayScore: null, period: null, clock: null, ...over,
});

describe("updated line", () => {
  it("gives the time with the timezone, in the viewer's zone", () => {
    expect(plain(ny.clockTime(updatedAt))).toBe("1:05 PM EST");
    expect(plain(london.clockTime(updatedAt))).toBe("18:05 GMT");
  });

  it("gives the age, ticking from now to minutes to hours", () => {
    expect(ny.ago(updatedAt, t + 20_000)).toBe("now");
    expect(ny.ago(updatedAt, t + 3 * 60_000 + 59_000)).toBe("3 min. ago");
    expect(ny.ago(updatedAt, t + 125 * 60_000)).toBe("2 hr. ago");
    expect(ny.ago(updatedAt, t + 30 * 3_600_000)).toBe("yesterday");
  });

  it("never says the data is from the future when the clocks disagree", () => {
    expect(ny.ago(updatedAt, t - 90_000)).toBe("now");
  });
});

describe("isDelayed", () => {
  it("is true only once updatedAt is more than 15 minutes old", () => {
    expect(DELAYED_AFTER_MS).toBe(15 * 60_000);
    expect(isDelayed(updatedAt, t + 15 * 60_000)).toBe(false);
    expect(isDelayed(updatedAt, t + 15 * 60_000 + 1)).toBe(true);
    expect(isDelayed(updatedAt, t - 60_000)).toBe(false);
  });
});

describe("NFL Week header", () => {
  const week = { seasonType: "regular" as const, number: 12, firstKickoff: "2026-11-20T01:15:00Z", lastKickoff: "2026-11-24T01:15:00Z" };

  it("gives the dates of the first and last Game in the viewer's timezone", () => {
    // 8:15 PM ET on Thursday and Monday night in New York; already Friday and Tuesday in London.
    expect(plain(ny.weekRange(week))).toBe("Thu, Nov 19 – Mon, Nov 23");
    expect(plain(london.weekRange(week))).toBe("Fri 20 Nov – Tue 24 Nov");
  });

  it("names regular weeks by number and postseason weeks by round", () => {
    expect(weekTitle(week)).toBe("Week 12");
    expect(weekTitle({ seasonType: "postseason", number: 1 })).toBe("Wild Card");
    expect(weekTitle({ seasonType: "postseason", number: 5 })).toBe("Super Bowl");
    expect(weekTitle({ seasonType: "postseason", number: 9 })).toBe("Postseason week 9");
  });

  // ESPN's 2025 postseason had a Pro Bowl week 4 and the Super Bowl as week 5; 2026 has no Pro
  // Bowl week, so the Super Bowl is week 4. The Worker never serves a Pro Bowl week.
  it("calls postseason week 4 the Super Bowl, as in a season with no Pro Bowl week", () => {
    expect(weekTitle({ seasonType: "postseason", number: 3 })).toBe("Conference championships");
    expect(weekTitle({ seasonType: "postseason", number: 4 })).toBe("Super Bowl");
  });
});

describe("statusText", () => {
  it("gives Upcoming the kickoff day and time in the viewer's timezone", () => {
    expect(plain(statusText(game({}), ny).label)).toBe("Sun 1:00 PM");
    expect(plain(statusText(game({}), london).label)).toBe("Sun 18:00");
    expect(statusText(game({}), ny).kind).toBe("upcoming");
  });

  it("gives Live the quarter and clock", () => {
    expect(statusText(game({ status: "live", period: 3, clock: "7:32" }), ny)).toEqual({ kind: "live", label: "Q3 7:32" });
    expect(statusText(game({ status: "live", period: 5, clock: "4:10" }), ny).label).toBe("OT 4:10");
    expect(statusText(game({ status: "live", period: 6, clock: "1:00" }), ny).label).toBe("2OT 1:00");
  });

  it("calls the break at 0:00 halftime or the end of the quarter", () => {
    expect(statusText(game({ status: "live", period: 2, clock: "0:00" }), ny).label).toBe("Half");
    expect(statusText(game({ status: "live", period: 1, clock: "0:00" }), ny).label).toBe("End Q1");
  });

  it("copes with a Live Game whose clock is missing", () => {
    expect(statusText(game({ status: "live", period: null, clock: null }), ny).label).toBe("Live");
  });

  it("says Final", () => {
    expect(statusText(game({ status: "final" }), ny)).toEqual({ kind: "final", label: "Final" });
  });
});
