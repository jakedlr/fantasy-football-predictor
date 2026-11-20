import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { buildIndex, rankRows, type Row } from "../../src/page/model.js";
import { bugHTML, esc, rowHTML, stripOrder } from "../../src/page/render.js";
import { makeTimeFormat } from "../../src/page/time.js";
import type { Leaderboard } from "../../src/page/types.js";

const example: Leaderboard = JSON.parse(readFileSync(new URL("../../schema/examples/leaderboard.json", import.meta.url), "utf8"));
const fmt = makeTimeFormat({ locale: "en-US", timeZone: "America/New_York" });
const rows = rankRows(buildIndex(example), "ppr");
const row = (name: string) => rows.find((r) => r.player.name === name)!;
const plain = (s: string) => s.replace(/[\u202f\u00a0]/g, " ");

describe("rowHTML", () => {
  it("marks a Live row and fills its banked bar to the share scored", () => {
    const html = rowHTML(row("Example Receiver"), fmt);
    expect(html).toMatch(/^<tr class="live"/);
    expect(html).toContain("Q3 7:32");
    expect(html).toContain("vs CIN");
    expect(html).toContain("scaleX(0.624)"); // 12.1 of 19.4
    expect(html).toContain('aria-label="62% scored"');
  });

  it("fills a Final row's bar and leaves an Upcoming row's empty", () => {
    expect(rowHTML(row("Steelers D/ST"), fmt)).toContain("scaleX(1.000)");
    const kicker = plain(rowHTML(row("Example Kicker"), fmt));
    expect(kicker).toContain("scaleX(0.000)");
    expect(kicker).toContain("Sun 4:25 PM");
    expect(kicker).toContain('aria-label="not kicked off"');
  });

  it("tags ESPN projections and players ruled Out", () => {
    expect(rowHTML(row("Example Kicker"), fmt)).toContain("ESPN projection");
    expect(rowHTML(row("Example Receiver"), fmt)).not.toContain("ESPN projection");
    const back = rowHTML(row("Example Back"), fmt);
    expect(back).toContain('class="upcoming out"');
    expect(back).toContain(">Out<");
    expect(back).toContain("<b>0.0</b>"); // Out shows 0, not a dash
  });

  it("shows a dash, not a rank or 0.0, for an Upcoming player with no Expected Points yet", () => {
    const r: Row = { ...row("Example Kicker"), points: { week: 0, soFar: null, expected: null } };
    const html = rowHTML(r, fmt);
    expect(html).toContain("<b>—</b>");
    expect(html).toContain('<td class="c-rank"><span class="none"');
  });

  it("escapes names", () => {
    const r: Row = { ...row("Example Receiver"), player: { ...row("Example Receiver").player, name: '<img src=x onerror="x">' } };
    expect(rowHTML(r, fmt)).not.toContain("<img");
    expect(esc(`a&b<'">`)).toBe("a&amp;b&lt;&#39;&quot;&gt;");
  });
});

describe("the Game strip", () => {
  it("puts Live first, then Upcoming by kickoff, then Final", () => {
    expect(stripOrder(example.games).map((g) => g.status)).toEqual(["live", "upcoming", "final"]);
  });

  it("shows each team's score and dims the loser of a Final", () => {
    const html = bugHTML(example.games[0]!, fmt, false);
    expect(html).toContain('<span class="t lose"><span>CLE</span><span>17</span></span>');
    expect(html).toContain('<span class="t"><span>PIT</span><span>24</span></span>');
    expect(html).toContain('aria-label="CLE at PIT, 17 to 24, Final"');
  });

  it("marks the chosen Game", () => {
    expect(bugHTML(example.games[1]!, fmt, true)).toContain('aria-pressed="true"');
    expect(bugHTML(example.games[1]!, fmt, false)).toContain("Q3<br>7:32");
  });
});
