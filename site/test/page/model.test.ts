import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  bankedFraction, buildIndex, filterEntries, formatPoints, opponentText, query, rankRows, ruleLabel, showEspnTag,
  sortRows, type Entry, type ViewState,
} from "../../src/page/model.js";
import type { Game, Leaderboard, Player } from "../../src/page/types.js";
import { generateLeaderboard } from "./fixture.js";

const example: Leaderboard = JSON.parse(readFileSync(new URL("../../schema/examples/leaderboard.json", import.meta.url), "utf8"));
const big = generateLeaderboard();
const view = (over: Partial<ViewState> = {}): ViewState => ({ pos: "All", q: "", rule: "ppr", game: null, sort: "week", dir: "desc", ...over });
const names = (rows: { player: Player }[]) => rows.map((r) => r.player.name);

describe("the generated fixture", () => {
  it("lists about 700 players across every Game Status", () => {
    expect(big.players.length).toBeGreaterThan(650);
    expect(big.players.length).toBeLessThan(800);
    expect(new Set(big.games.map((g) => g.status))).toEqual(new Set(["final", "live", "upcoming"]));
  });
});

describe("filterEntries", () => {
  const entries = buildIndex(big);

  it("keeps every player under All", () => {
    expect(filterEntries(entries, view())).toHaveLength(big.players.length);
  });

  it.each(["QB", "RB", "WR", "TE", "K", "D/ST"] as const)("keeps only %s under %s", (pos) => {
    const kept = filterEntries(entries, view({ pos }));
    expect(kept.length).toBeGreaterThan(0);
    expect(kept.every((e) => e.player.position === pos)).toBe(true);
  });

  it("keeps RB, WR and TE under FLEX", () => {
    const kept = filterEntries(entries, view({ pos: "FLEX" }));
    expect(new Set(kept.map((e) => e.player.position))).toEqual(new Set(["RB", "WR", "TE"]));
    expect(kept).toHaveLength(big.players.filter((p) => ["RB", "WR", "TE"].includes(p.position)).length);
  });

  it("searches the name, ignoring case and punctuation", () => {
    const ex = buildIndex(example);
    expect(names(filterEntries(ex, view({ q: "example rec" })))).toEqual(["Example Receiver"]);
    expect(names(filterEntries(ex, view({ q: "STEELERS" })))).toEqual(["Steelers D/ST"]);
    expect(names(filterEntries(ex, view({ q: "dst" })))).toEqual(["Steelers D/ST"]);
    const oneal = big.players.find((p) => p.name.includes("O'Neal"));
    if (oneal) expect(names(filterEntries(entries, view({ q: "oneal" })))).toContain(oneal.name);
  });

  it("searches the team by abbreviation, nickname and city", () => {
    const ex = buildIndex(example);
    expect(names(filterEntries(ex, view({ q: "bal" })))).toEqual(["Example Receiver"]);
    expect(names(filterEntries(ex, view({ q: "ravens" })))).toEqual(["Example Receiver"]);
    expect(names(filterEntries(ex, view({ q: "san francisco" })))).toEqual(["Example Kicker"]);
    expect(names(filterEntries(ex, view({ q: "49ers" })))).toEqual(["Example Kicker"]);
  });

  it("matches the start of words, not the middle", () => {
    // "ne" finds Neal and the Patriots (NE) but not Jones.
    const p = (name: string, team: string): Player => ({
      id: "sleeper:1", name, team, position: "WR", gameId: "g", out: false, expectedSource: null,
      points: { ppr: { week: 1, soFar: 1, expected: null } },
    });
    const board = { ...example, games: [{ ...example.games[0]!, id: "g", home: "NE", away: "SF" }], players: [p("A. Jones", "SF"), p("B. Neal", "SF"), p("C. Smith", "NE")] };
    expect(names(filterEntries(buildIndex(board), view({ q: "ne" })))).toEqual(["B. Neal", "C. Smith"]);
    expect(names(filterEntries(buildIndex(board), view({ q: "ones" })))).toEqual([]);
    expect(names(filterEntries(buildIndex(board), view({ q: "patriots" })))).toEqual(["C. Smith"]);
  });

  it("combines search with the position filter and every word must match", () => {
    const kept = filterEntries(entries, view({ pos: "WR", q: "chiefs" }));
    expect(kept.length).toBeGreaterThan(0);
    expect(kept.every((e) => e.player.position === "WR" && e.player.team === "KC")).toBe(true);
    expect(filterEntries(entries, view({ q: "chiefs ravens" }))).toHaveLength(0);
  });

  it("keeps one Game's players when a Game is chosen", () => {
    const game = big.games[3]!;
    const kept = filterEntries(entries, view({ game: game.id }));
    expect(kept.length).toBeGreaterThan(0);
    expect(kept.every((e) => e.player.gameId === game.id)).toBe(true);
  });

  it("filters 700 players in well under 100 ms", () => {
    const t0 = performance.now();
    for (const q of ["", "a", "ab", "abb", "chiefs", "k"]) query(entries, view({ q, pos: "FLEX" }));
    expect((performance.now() - t0) / 6).toBeLessThan(20);
  });
});

describe("rankRows and sortRows", () => {
  const ex = buildIndex(example);

  it("ranks by Week Points under the chosen Scoring Rules", () => {
    expect(names(rankRows(ex, "ppr"))).toEqual(["Example Receiver", "Steelers D/ST", "Example Kicker", "Example Back"]);
    expect(rankRows(ex, "ppr").map((r) => r.rank)).toEqual([1, 2, 3, 4]);
    expect(rankRows(ex, "espn-league")[0]!.points.week).toBe(19.4);
  });

  it("gives tied Week Points the same rank, then skips", () => {
    const entries = buildIndex(example).map((e, i): Entry => ({
      ...e, player: { ...e.player, points: { ppr: { week: i < 2 ? 10 : 5 - i, soFar: 1, expected: null } } },
    }));
    expect(rankRows(entries, "ppr").map((r) => r.rank)).toEqual([1, 1, 3, 4]);
  });

  it("breaks ties by Points So Far, then name, so the order is stable", () => {
    const entries = buildIndex(example).map((e, i): Entry => ({
      ...e, player: { ...e.player, name: `P${3 - i}`, points: { ppr: { week: 10, soFar: i === 3 ? 9 : 2, expected: null } } },
    }));
    expect(names(rankRows(entries, "ppr"))).toEqual(["P0", "P1", "P2", "P3"]);
  });

  it("sorts by any column and keeps the rank", () => {
    const rows = rankRows(ex, "ppr");
    expect(names(sortRows(rows, "name", "asc"))).toEqual(["Example Back", "Example Kicker", "Example Receiver", "Steelers D/ST"]);
    expect(names(sortRows(rows, "week", "asc"))).toEqual(["Example Back", "Example Kicker", "Steelers D/ST", "Example Receiver"]);
    expect(sortRows(rows, "team", "asc").map((r) => r.player.team)).toEqual(["BAL", "PIT", "SEA", "SF"]);
    expect(sortRows(rows, "pos", "asc").map((r) => r.player.position)).toEqual(["D/ST", "K", "RB", "WR"]);
    expect(sortRows(rows, "opp", "asc").map((r) => r.player.name)).toEqual(["Example Receiver", "Steelers D/ST", "Example Kicker", "Example Back"]);
    expect(sortRows(rows, "rank", "desc").map((r) => r.rank)).toEqual([4, 3, 2, 1]);
  });

  it("sorts Points So Far with missing values last either way", () => {
    const rows = rankRows(ex, "ppr");
    expect(names(sortRows(rows, "soFar", "desc")).slice(0, 2)).toEqual(["Example Receiver", "Steelers D/ST"]);
    expect(names(sortRows(rows, "soFar", "asc")).slice(0, 2)).toEqual(["Steelers D/ST", "Example Receiver"]);
  });

  it("sorts Game Status Live, then Upcoming by kickoff, then Final", () => {
    const rows = rankRows(ex, "ppr");
    expect(sortRows(rows, "status", "asc").map((r) => r.game.status)).toEqual(["live", "upcoming", "upcoming", "final"]);
  });

  it("query filters, ranks within the filter, then sorts", () => {
    const rows = query(buildIndex(big), view({ pos: "QB", sort: "name", dir: "asc" }));
    expect(rows.every((r) => r.player.position === "QB")).toBe(true);
    expect([...rows].sort((a, b) => a.rank - b.rank)[0]!.rank).toBe(1);
    const sortedNames = names(rows);
    expect(sortedNames).toEqual([...sortedNames].sort((a, b) => a.localeCompare(b)));
  });
});

describe("opponentText", () => {
  const game = example.games[1]!; // CIN at BAL
  it("says vs for the home team and @ for the away team", () => {
    expect(opponentText("BAL", game)).toBe("vs CIN");
    expect(opponentText("CIN", game)).toBe("@ BAL");
  });
});

describe("bankedFraction", () => {
  const g = (status: Game["status"]) => ({ status });
  it("is full when Final", () => {
    expect(bankedFraction({ week: 9, soFar: 9, expected: 6.8 }, g("final"))).toBe(1);
    expect(bankedFraction({ week: -1, soFar: -1, expected: null }, g("final"))).toBe(1);
  });
  it("is the share already scored when Live", () => {
    expect(bankedFraction({ week: 20, soFar: 5, expected: 16 }, g("live"))).toBe(0.25);
    expect(bankedFraction({ week: 12.1, soFar: 12.1, expected: null }, g("live"))).toBe(1);
  });
  it("stays between 0 and 1", () => {
    expect(bankedFraction({ week: 3, soFar: 4, expected: 1 }, g("live"))).toBe(1);
    expect(bankedFraction({ week: 2, soFar: -1, expected: 6 }, g("live"))).toBe(0);
    expect(bankedFraction({ week: 0, soFar: 0, expected: null }, g("live"))).toBe(0);
  });
  it("is empty when Upcoming", () => {
    expect(bankedFraction({ week: 8.3, soFar: null, expected: 8.3 }, g("upcoming"))).toBe(0);
    expect(bankedFraction({ week: 0, soFar: null, expected: null }, g("upcoming"))).toBe(0);
  });
});

describe("labels", () => {
  it("formats points to one decimal, with a dash when missing", () => {
    expect(formatPoints(19.4)).toBe("19.4");
    expect(formatPoints(0)).toBe("0.0");
    expect(formatPoints(-1.25)).toBe("−1.3");
    expect(formatPoints(null)).toBe("—");
    expect(formatPoints(-0.01)).toBe("0.0");
  });
  it("names the Scoring Rules", () => {
    expect(["ppr", "half-ppr", "standard", "espn-league"].map(ruleLabel)).toEqual(["PPR", "Half PPR", "Standard", "ESPN league"]);
    expect(ruleLabel("six-pt-pass")).toBe("Six pt pass");
  });
  it("tags an ESPN projection only while it still counts toward Week Points", () => {
    const p = example.players[2]!; // ESPN, Upcoming
    expect(showEspnTag(p, { status: "upcoming" })).toBe(true);
    expect(showEspnTag(p, { status: "live" })).toBe(true);
    expect(showEspnTag(p, { status: "final" })).toBe(false);
    expect(showEspnTag(example.players[0]!, { status: "live" })).toBe(false);
  });
});
