import { describe, expect, it } from "vitest";
import { KICKOFF_LEAD_MS, MAX_WAIT_MS, POLL_MS, nextFetchIn, refetchOnShow } from "../../src/page/poll.js";
import type { Game } from "../../src/page/types.js";

const MIN = 60_000;
const now = Date.parse("2026-11-22T17:00:00Z");
const at = (ms: number) => new Date(now + ms).toISOString();
const game = (status: Game["status"], kickoffIn: number): Pick<Game, "status" | "kickoff"> => ({ status, kickoff: at(kickoffIn) });
const plan = (games: Pick<Game, "status" | "kickoff">[], over: { lastFetchAt?: number; visible?: boolean } = {}) =>
  nextFetchIn({ games, now, lastFetchAt: over.lastFetchAt ?? now, visible: over.visible ?? true });

describe("nextFetchIn", () => {
  it("uses the PLAN's numbers", () => {
    expect(POLL_MS).toBe(5 * MIN);
    expect(KICKOFF_LEAD_MS).toBe(10 * MIN);
  });

  it("polls every 5 minutes while a Game is Live", () => {
    expect(plan([game("final", -200 * MIN), game("live", -60 * MIN), game("upcoming", 3 * 3600_000)])).toBe(5 * MIN);
  });

  it("counts from the last fetch, so a fetch two minutes ago is due in three", () => {
    expect(plan([game("live", -60 * MIN)], { lastFetchAt: now - 2 * MIN })).toBe(3 * MIN);
    expect(plan([game("live", -60 * MIN)], { lastFetchAt: now - 9 * MIN })).toBe(0);
  });

  it("polls every 5 minutes when a Game kicks off within 10 minutes", () => {
    expect(plan([game("upcoming", 10 * MIN)])).toBe(5 * MIN);
    expect(plan([game("upcoming", 4 * MIN)])).toBe(5 * MIN);
  });

  it("keeps polling when kickoff has passed but the Game still shows Upcoming", () => {
    expect(plan([game("upcoming", -20 * MIN)])).toBe(5 * MIN);
  });

  it("stops polling a Game stuck at Upcoming hours after kickoff (postponed)", () => {
    expect(plan([game("upcoming", -5 * 3600_000)])).toBeNull();
  });

  it("otherwise waits until 10 minutes before the next kickoff", () => {
    expect(plan([game("final", -3 * 3600_000), game("upcoming", 3 * 3600_000), game("upcoming", 2 * 3600_000)]))
      .toBe(2 * 3600_000 - 10 * MIN);
  });

  it("caps a long wait so a sleeping laptop wakes to a fresh plan", () => {
    expect(MAX_WAIT_MS).toBe(6 * 3600_000);
    expect(plan([game("upcoming", 3 * 24 * 3600_000)])).toBe(MAX_WAIT_MS);
  });

  it("stops when every Game is Final, and for an empty week", () => {
    expect(plan([game("final", -3 * 3600_000), game("final", -26 * 3600_000)])).toBeNull();
    expect(plan([])).toBeNull();
  });

  it("never polls while the tab is hidden", () => {
    expect(plan([game("live", -30 * MIN)], { visible: false })).toBeNull();
    expect(plan([game("upcoming", 5 * MIN)], { visible: false })).toBeNull();
    expect(plan([game("upcoming", 3 * 3600_000)], { visible: false })).toBeNull();
  });
});

describe("refetchOnShow", () => {
  it("refetches when the tab is shown and the last fetch is more than 5 minutes old", () => {
    expect(refetchOnShow(now - 5 * MIN - 1, now)).toBe(true);
    expect(refetchOnShow(now - 5 * MIN, now)).toBe(false);
    expect(refetchOnShow(now - 30_000, now)).toBe(false);
  });
});
