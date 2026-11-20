/**
 * How an accepted push becomes KV `expected` and `idmap` (kv.ts `ExpectedStore`, `IdMapStore`).
 * Pure: the Function reads and writes KV.
 */
import type { Expectation, ExpectedStore } from "../worker/kv.js";
import { leaderboardId, type Push } from "./validate.js";

/**
 * The store after `push`. Another season, seasonType or week replaces it. For the same week, a
 * stored player whose kickoff is at or before the push's generatedAt keeps his entry (frozen at
 * kickoff, even if the push lists him again); every other player is the push's entry, so a stored
 * player the push leaves out before his kickoff is dropped.
 */
export function mergeExpected(stored: ExpectedStore | null, push: Push): ExpectedStore {
  const players: Record<string, Expectation> = {};
  const sameWeek =
    stored !== null && stored.season === push.season && stored.seasonType === push.seasonType && stored.week === push.week;
  const asOf = Date.parse(push.generatedAt);
  if (sameWeek) {
    for (const [id, e] of Object.entries(stored.players)) if (Date.parse(e.kickoff) <= asOf) players[id] = e;
  }
  for (const p of push.players) {
    const id = leaderboardId(p);
    if (players[id] !== undefined) continue;
    players[id] = { name: p.name, team: p.team, position: p.position, kickoff: p.kickoff, statMeans: p.statMeans, expected: p.expected };
  }
  return {
    schemaVersion: 1,
    season: push.season,
    seasonType: push.seasonType,
    week: push.week,
    generatedAt: push.generatedAt,
    players,
  };
}

/** The push's Sleeper → ESPN map turned round. An ESPN id two Sleeper ids claim is left out. */
export function invertIdMap(idMap: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  const clashed = new Set<string>();
  for (const [sleeper, espn] of Object.entries(idMap)) {
    if (clashed.has(espn)) continue;
    if (Object.hasOwn(out, espn)) {
      delete out[espn];
      clashed.add(espn);
    } else {
      out[espn] = sleeper;
    }
  }
  return out;
}

export function sameIdMap(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((k) => Object.hasOwn(b, k) && a[k] === b[k]);
}
