/**
 * HTML for the Leaderboard's repeated parts: table rows, the Game strip, the skeleton.
 * Strings, not DOM, so a 50-row render is one innerHTML write. Every data value is escaped.
 */
import { bankedFraction, formatPoints, opponentText, showEspnTag, type Row } from "./model.js";
import { statusText, type TimeFormat } from "./time.js";
import type { Game } from "./types.js";

export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

export function rowHTML(r: Row, fmt: TimeFormat): string {
  const { player: p, game: g, points } = r;
  const st = statusText(g, fmt);
  const share = bankedFraction(points, g);
  // Upcoming with no Expected Points yet: nothing to rank by, so a quiet dash rather than a fake 0.0.
  const noEstimate = g.status === "upcoming" && points.expected === null && !p.out;
  const week = noEstimate ? "—" : formatPoints(points.week);
  const tags = (p.out ? '<span class="tag tag-out">Out</span>' : "")
    + (showEspnTag(p, g) ? '<span class="tag" title="Expected Points from ESPN’s projection, rescored under these Scoring Rules">ESPN projection</span>' : "");
  const pct = Math.round(share * 100);
  const bankLabel = g.status === "upcoming" ? "not kicked off" : `${pct}% scored`;
  return `<tr class="${g.status}${p.out ? " out" : ""}" data-id="${esc(p.id)}">`
    // Ranking players who have nothing to rank by would give dozens of tied ranks; leave it blank.
    + `<td class="c-rank">${noEstimate ? '<span class="none" title="No Expected Points yet">—</span>' : r.rank}</td>`
    + `<td class="c-player"><span class="name">${esc(p.name)}</span>${tags}</td>`
    + `<td class="c-team">${esc(p.team)}</td>`
    + `<td class="c-pos">${esc(p.position)}</td>`
    + `<td class="c-opp">${esc(opponentText(p.team, g))}</td>`
    + `<td class="c-status">${st.kind === "live" ? '<span class="dot" aria-hidden="true"></span>' : ""}${esc(st.label)}</td>`
    + `<td class="c-week num${noEstimate ? " none" : ""}"${noEstimate ? ' title="No Expected Points yet"' : ""}>`
    + `<span class="wp"><span class="bank" role="img" aria-label="${bankLabel}"><i style="transform:scaleX(${share.toFixed(3)})"></i></span>`
    + `<b>${week}</b></span></td>`
    + `<td class="c-sofar num">${formatPoints(points.soFar)}</td></tr>`;
}

const STRIP_ORDER = { live: 0, upcoming: 1, final: 2 } as const;

/** Live first, then Upcoming by kickoff, then Final: what's happening now leads, like a broadcast ticker. */
export function stripOrder(games: Game[]): Game[] {
  return [...games].sort((a, b) => STRIP_ORDER[a.status] - STRIP_ORDER[b.status] || Date.parse(a.kickoff) - Date.parse(b.kickoff));
}

export function bugHTML(g: Game, fmt: TimeFormat, selected: boolean): string {
  const st = statusText(g, fmt);
  const final = g.status === "final";
  const lose = (mine: number | null, theirs: number | null) => (final && mine !== null && theirs !== null && mine < theirs ? " lose" : "");
  let side: string;
  if (g.status === "upcoming") {
    const [day, ...time] = st.label.split(" ");
    side = `${esc(day ?? "")}<br>${esc(time.join(" "))}`;
  } else if (g.status === "live") {
    const [q, ...clock] = st.label.split(" ");
    side = `${esc(q ?? "")}<br>${esc(clock.join(" "))}`;
  } else side = "Final";
  const word = { live: "Live", upcoming: "Upcoming", final: "Final" }[g.status];
  const score = g.awayScore === null ? "" : `, ${g.awayScore} to ${g.homeScore}`;
  return `<button type="button" class="bug ${g.status}" data-game="${esc(g.id)}" aria-pressed="${selected}"`
    + ` aria-label="${esc(`${g.away} at ${g.home}${score}, ${word} ${st.kind === "final" ? "" : st.label}`.trim())}">`
    + `<span class="t${lose(g.awayScore, g.homeScore)}"><span>${esc(g.away)}</span><span>${g.awayScore ?? ""}</span></span>`
    + `<span class="t${lose(g.homeScore, g.awayScore)}"><span>${esc(g.home)}</span><span>${g.homeScore ?? ""}</span></span>`
    + `<span class="s">${side}</span></button>`;
}

export function skeletonRowsHTML(n: number): string {
  const bar = (w: number) => `<span class="sk" style="width:${w}px"></span>`;
  let html = "";
  for (let i = 0; i < n; i++) {
    html += `<tr class="skeleton" aria-hidden="true"><td class="c-rank">${bar(16)}</td><td class="c-player">${bar(110 + ((i * 37) % 60))}</td>`
      + `<td class="c-team">${bar(28)}</td><td class="c-pos">${bar(22)}</td><td class="c-opp">${bar(44)}</td>`
      + `<td class="c-status">${bar(64)}</td><td class="c-week num">${bar(84)}</td><td class="c-sofar num">${bar(32)}</td></tr>`;
  }
  return html;
}

export function messageRowHTML(html: string): string {
  return `<tr class="message"><td colspan="8">${html}</td></tr>`;
}
