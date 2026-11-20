/**
 * The Leaderboard page: fetches /api/leaderboard, renders it, and keeps it fresh on the schedule in
 * poll.ts. All decisions live in the pure modules; this file only wires them to the DOM.
 */
import {
  DEFAULT_DIR, POSITION_FILTERS, buildIndex, query, ruleLabel,
  type Entry, type PositionFilter, type SortDir, type SortKey, type ViewState,
} from "./model.js";
import { nextFetchIn, refetchOnShow } from "./poll.js";
import { bugHTML, esc, messageRowHTML, rowHTML, skeletonRowsHTML, stripOrder } from "./render.js";
import { isDelayed, makeTimeFormat, weekTitle } from "./time.js";
import type { Leaderboard } from "./types.js";

const API = "/api/leaderboard";
const PAGE = 50;
const RULE_KEY = "ffp.rule";

const $ = <T extends HTMLElement>(sel: string) => document.querySelector(sel) as T;
const fmt = makeTimeFormat();
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

const el = {
  root: document.documentElement,
  weekTitle: $("#week-title"),
  weekDates: $("#week-dates"),
  updated: $("#updated"),
  updatedTime: $("#updated-time"),
  updatedAgo: $("#updated-ago"),
  strip: $("#strip"),
  banner: $("#banner"),
  bannerAgo: $("#banner-ago"),
  chips: $("#positions"),
  rules: $("#rules"),
  q: $<HTMLInputElement>("#q"),
  sort: $<HTMLSelectElement>("#sort-select"),
  shown: $("#shown"),
  caption: $("#caption"),
  rows: $<HTMLTableSectionElement>("#rows"),
  more: $<HTMLButtonElement>("#more"),
};

let data: Leaderboard | null = null;
let entries: Entry[] = [];
let lastFetchAt = 0;
let timer: number | undefined;
let inFlight = false;
let limit = PAGE;
const view: ViewState = { pos: "All", q: "", rule: readRule() ?? "ppr", game: null, sort: "week", dir: "desc" };

function readRule(): string | null {
  try { return localStorage.getItem(RULE_KEY); } catch { return null; }
}
function saveRule(rule: string) {
  try { localStorage.setItem(RULE_KEY, rule); } catch { /* private mode: the default is fine */ }
}

/* ---------- fetching and the poll schedule ---------- */

async function load() {
  if (inFlight) return;
  inFlight = true;
  lastFetchAt = Date.now();
  try {
    const res = await fetch(API, { headers: { accept: "application/json" }, cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const next = (await res.json()) as Leaderboard;
    if (next?.schemaVersion !== 1 || !Array.isArray(next.players) || !Array.isArray(next.games)) throw new Error("unexpected shape");
    const first = data === null;
    data = next;
    entries = buildIndex(next);
    if (!next.scoringRules.includes(view.rule)) view.rule = next.scoringRules[0] ?? "ppr";
    if (view.game && !next.games.some((g) => g.id === view.game)) view.game = null;
    renderAll(!first);
  } catch {
    // The first load shows the error; later failures keep the last good data, and the
    // "Data delayed" banner speaks once it is 15 minutes old.
    if (data === null) showError();
  } finally {
    inFlight = false;
    schedule();
  }
}

function schedule() {
  clearTimeout(timer);
  const delay = nextFetchIn({ games: data?.games ?? [], now: Date.now(), lastFetchAt, visible: !document.hidden });
  if (delay !== null) timer = window.setTimeout(load, delay);
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) { clearTimeout(timer); return; }
  tick();
  if (refetchOnShow(lastFetchAt, Date.now())) void load();
  else schedule();
});

/* ---------- rendering ---------- */

function renderAll(animate: boolean) {
  if (!data) return;
  el.root.dataset.state = "ready";
  el.weekTitle.textContent = `${weekTitle(data.week)} Leaderboard`;
  el.weekDates.textContent = data.games.length ? fmt.weekRange(data.week) : "";
  renderRules();
  renderStrip();
  renderTable(animate);
  tick();
}

/* The four PLAN options are in the page from the start, so nothing shifts when the data lands. */
function renderRules() {
  if (!data) return;
  const present = [...el.rules.querySelectorAll<HTMLInputElement>("input")].map((i) => i.value);
  const html = data.scoringRules.map((r) =>
    `<label><input type="radio" name="rule" value="${esc(r)}"${r === view.rule ? " checked" : ""}>${esc(ruleLabel(r))}</label>`).join("");
  if (present.join() !== data.scoringRules.join()) el.rules.innerHTML = html;
  for (const input of el.rules.querySelectorAll<HTMLInputElement>("input")) input.checked = input.value === view.rule;
}

function renderStrip() {
  if (!data) return;
  const games = stripOrder(data.games);
  el.strip.innerHTML = games.length
    ? `<button type="button" class="bug all" data-game="" aria-pressed="${view.game === null}">All<br>Games</button>`
      + games.map((g) => bugHTML(g, fmt, g.id === view.game)).join("")
    : "";
  el.strip.hidden = games.length === 0;
  el.root.classList.toggle("has-live", data.games.some((g) => g.status === "live"));
}

function renderTable(animate = false) {
  if (!data) return;
  const rows = query(entries, view);
  const shown = rows.slice(0, limit);
  const before = animate ? positions() : null;

  if (data.players.length === 0) {
    el.rows.innerHTML = messageRowHTML(emptyWeekHTML());
  } else if (rows.length === 0) {
    const game = view.game ? data.games.find((g) => g.id === view.game) : null;
    el.rows.innerHTML = messageRowHTML(
      `<strong>No players match${view.q ? ` “${esc(view.q.trim())}”` : ""}${view.pos !== "All" ? ` at ${esc(view.pos)}` : ""}${game ? ` in ${esc(game.away)} at ${esc(game.home)}` : ""}.</strong> `
      + `Try a last name or a team, like “BAL” or “Ravens”. <button type="button" class="link" data-clear>Clear filters</button>`);
  } else {
    el.rows.innerHTML = shown.map((r) => rowHTML(r, fmt)).join("");
  }
  if (before) slide(before);

  const left = rows.length - shown.length;
  el.more.hidden = left <= 0;
  el.more.textContent = `Show ${Math.min(left, PAGE)} more`;
  el.shown.textContent = rows.length === 0 ? "No players"
    : shown.length === rows.length ? `${rows.length} ${rows.length === 1 ? "player" : "players"}`
    : `1–${shown.length} of ${rows.length} players`;
  el.caption.textContent = `${weekTitle(data.week)} Leaderboard, ${ruleLabel(view.rule)}, sorted by ${sortName(view.sort)}`;

  for (const b of el.chips.querySelectorAll<HTMLButtonElement>("[data-pos]")) b.setAttribute("aria-pressed", String(b.dataset.pos === view.pos));
  for (const b of el.strip.querySelectorAll<HTMLButtonElement>("[data-game]")) b.setAttribute("aria-pressed", String((b.dataset.game || null) === view.game));
  for (const b of document.querySelectorAll<HTMLButtonElement>("th [data-sort]")) {
    const on = b.dataset.sort === view.sort;
    b.closest("th")!.setAttribute("aria-sort", on ? (view.dir === "asc" ? "ascending" : "descending") : "none");
    b.dataset.dir = on ? view.dir : "";
  }
  const value = `${view.sort}:${view.dir}`;
  if (![...el.sort.options].some((o) => o.value === value)) {
    const o = new Option(`${sortName(view.sort)} ${view.dir === "asc" ? "↑" : "↓"}`, value);
    o.dataset.extra = "";
    el.sort.querySelectorAll("[data-extra]").forEach((x) => x.remove());
    el.sort.add(o);
  }
  el.sort.value = value;
}

function sortName(key: SortKey): string {
  return { rank: "Rank", name: "Player", team: "Team", pos: "Position", opp: "Opponent", status: "Game Status", week: "Week Points", soFar: "Points So Far" }[key];
}

function emptyWeekHTML(): string {
  if (!data || data.games.length === 0) return "<strong>No Games this NFL Week.</strong> The Leaderboard returns with the next one.";
  const upcoming = data.games.filter((g) => g.status === "upcoming").sort((a, b) => Date.parse(a.kickoff) - Date.parse(b.kickoff))[0];
  return "<strong>No players yet.</strong> A player is listed once he records a stat or has Expected Points"
    + (upcoming ? `; the next Game kicks off ${esc(fmt.kickoff(upcoming.kickoff))}.` : ".");
}

/* Rank changes slide into place (after a Scoring Rules switch or a refresh), so you can see who moved. */
function positions(): Map<string, number> | null {
  if (reduceMotion.matches || document.hidden) return null;
  const map = new Map<string, number>();
  for (const tr of el.rows.rows) if (tr.dataset.id) map.set(tr.dataset.id, tr.getBoundingClientRect().top);
  return map;
}
function slide(before: Map<string, number>) {
  const vh = innerHeight;
  for (const tr of el.rows.rows) {
    const old = before.get(tr.dataset.id ?? "");
    if (old === undefined) continue;
    const top = tr.getBoundingClientRect().top;
    const dy = old - top;
    if (Math.abs(dy) < 1 || (top > vh && old > vh) || (top < -60 && old < -60)) continue;
    tr.animate([{ transform: `translateY(${dy}px)` }, { transform: "none" }], { duration: 280, easing: "cubic-bezier(0.77, 0, 0.175, 1)" });
  }
}

function showError() {
  el.root.dataset.state = "error";
  el.rows.innerHTML = messageRowHTML(
    "<strong>Couldn’t load the Leaderboard.</strong> Check your connection, then try again. "
    + '<button type="button" class="link" data-retry>Retry</button>');
  el.shown.textContent = "";
  el.more.hidden = true;
}

/* The freshness line and the "Data delayed" banner tick on their own; no network involved. */
function tick() {
  if (!data) return;
  const now = Date.now();
  el.updated.hidden = false;
  el.updatedTime.textContent = fmt.clockTime(data.updatedAt);
  el.updatedTime.setAttribute("datetime", data.updatedAt);
  el.updatedAgo.textContent = fmt.ago(data.updatedAt, now);
  const delayed = isDelayed(data.updatedAt, now);
  if (el.banner.hidden === delayed) el.banner.hidden = !delayed;
  el.bannerAgo.textContent = fmt.ago(data.updatedAt, now);
}
setInterval(() => { if (!document.hidden) tick(); }, 15_000);

/* ---------- controls ---------- */

el.chips.innerHTML = POSITION_FILTERS.map((p) =>
  `<button type="button" data-pos="${p}" aria-pressed="${p === view.pos}"${p === "FLEX" ? ' title="RB, WR and TE"' : ""}>${p}</button>`).join("");

el.chips.addEventListener("click", (e) => {
  const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-pos]");
  if (!b) return;
  view.pos = b.dataset.pos as PositionFilter;
  limit = PAGE;
  renderTable();
});
el.strip.addEventListener("click", (e) => {
  const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-game]");
  if (!b) return;
  const id = b.dataset.game || null;
  view.game = id === null || view.game === id ? null : id;
  limit = PAGE;
  renderTable();
});
el.rules.addEventListener("change", (e) => {
  const input = e.target as HTMLInputElement;
  view.rule = input.value;
  saveRule(view.rule);
  renderTable(true);
});
el.q.addEventListener("input", () => {
  view.q = el.q.value;
  limit = PAGE;
  renderTable();
});
document.querySelector("thead")!.addEventListener("click", (e) => {
  const b = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-sort]");
  if (!b) return;
  const key = b.dataset.sort as SortKey;
  view.dir = view.sort === key ? (view.dir === "asc" ? "desc" : "asc") : DEFAULT_DIR[key];
  view.sort = key;
  renderTable();
});
el.sort.addEventListener("change", () => {
  const [key, dir] = el.sort.value.split(":") as [SortKey, SortDir];
  view.sort = key;
  view.dir = dir;
  renderTable();
});
el.more.addEventListener("click", () => {
  limit += PAGE;
  renderTable();
});
el.rows.addEventListener("click", (e) => {
  const t = e.target as HTMLElement;
  if (t.closest("[data-retry]")) {
    el.root.dataset.state = "loading";
    el.rows.innerHTML = skeletonRowsHTML(12);
    void load();
  } else if (t.closest("[data-clear]")) {
    Object.assign(view, { pos: "All", q: "", game: null });
    el.q.value = "";
    renderStrip();
    renderTable();
  }
});

el.rows.innerHTML = skeletonRowsHTML(12);
void load();
