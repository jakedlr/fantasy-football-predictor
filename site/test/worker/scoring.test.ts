import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  fgMadeYardsFromSleeperBuckets,
  pointsAllowed,
  RULES,
  RULES_NAMES,
  score,
  scoreDst,
  scoreKicker,
  tierPoints,
  type ScoringRules,
  type Tier,
} from "../../src/worker/scoring.js";

const read = (path: string) => JSON.parse(readFileSync(new URL(`../../${path}`, import.meta.url), "utf8"));
const contractRules: Record<string, ScoringRules> = read("contracts/scoring-rules.json").rules;
const cases: { kind: "offense" | "kicker" | "dst"; name: string; stats: Record<string, unknown>; expected: Record<string, number> }[] =
  read("contracts/scoring-cases.json").cases;

const SCORERS = {
  offense: (stats: any, rules: ScoringRules) => score(stats, rules),
  kicker: (stats: any, rules: ScoringRules) => scoreKicker(stats, rules),
  dst: (stats: any, rules: ScoringRules) => scoreDst(stats, rules),
};

describe("the shared scoring cases (ADR 0005)", () => {
  it("cover every kind and every Leaderboard Scoring Rules option", () => {
    expect(new Set(cases.map((c) => c.kind))).toEqual(new Set(["offense", "kicker", "dst"]));
    for (const c of cases) expect(Object.keys(c.expected).sort()).toEqual(Object.keys(contractRules).sort());
  });

  for (const c of cases) {
    for (const name of Object.keys(c.expected)) {
      it(`${c.kind}: ${c.name} under ${name}`, () => {
        const got = SCORERS[c.kind](c.stats, contractRules[name]!);
        expect(Math.abs(got - c.expected[name]!), `got ${got}, expected ${c.expected[name]}`).toBeLessThanOrEqual(1e-6);
      });
    }
  }
});

describe("RULES", () => {
  it("are the contract's rules, default first", () => {
    expect(RULES).toEqual(contractRules);
    expect(RULES_NAMES[0]).toBe(read("contracts/scoring-rules.json").default);
    expect([...RULES_NAMES].sort()).toEqual(Object.keys(contractRules).sort());
  });
});

describe("tierPoints matches Python's _tier_points", () => {
  const closed: Tier[] = [
    { min: 0, max: 9, points: 5 },
    { min: 10, max: 19, points: 2 },
  ];
  const open: Tier[] = [...closed.slice(0, 1), { min: 10, max: null, points: -1 }];

  it("is the last tier whose min the value reaches", () => {
    expect(tierPoints(0, closed)).toBe(5);
    expect(tierPoints(9, closed)).toBe(5);
    expect(tierPoints(10, closed)).toBe(2);
    expect(tierPoints(19, closed)).toBe(2);
  });

  it("gives a value below the first tier the first tier's points", () => {
    expect(tierPoints(-12, closed)).toBe(5);
  });

  it("gives a value above a closed last tier nothing, and an open last tier keeps going", () => {
    expect(tierPoints(19.5, closed)).toBe(0);
    expect(tierPoints(20, closed)).toBe(0);
    expect(tierPoints(500, open)).toBe(-1);
  });

  it("puts a fractional value between tiers in the lower tier", () => {
    expect(tierPoints(9.5, closed)).toBe(5);
    expect(tierPoints(9.999, closed)).toBe(5);
  });

  it("is 0 with no tiers", () => {
    expect(tierPoints(3, [])).toBe(0);
  });
});

describe("pointsAllowed", () => {
  const rules = (excludes: string[]) => ({ ...contractRules.ppr!, dst: { ...contractRules.ppr!.dst, points_allowed_excludes: excludes } });
  const line = { points_allowed_all: 31, opponent_defensive_tds_points: 7, opponent_return_tds_points: 8 };

  it("leaves out only what the rules exclude", () => {
    expect(pointsAllowed(line, rules([]))).toBe(31);
    expect(pointsAllowed(line, rules(["opponent_defensive_tds"]))).toBe(24);
    expect(pointsAllowed(line, rules(["opponent_return_tds"]))).toBe(23);
    expect(pointsAllowed(line, rules(["opponent_defensive_tds", "opponent_return_tds"]))).toBe(16);
  });

  it("counts missing stats as 0", () => {
    expect(pointsAllowed({}, rules(["opponent_defensive_tds"]))).toBe(0);
  });
});

describe("fgMadeYardsFromSleeperBuckets", () => {
  it("scores each kick at its bucket's min", () => {
    expect(fgMadeYardsFromSleeperBuckets({ fgm_20_29: 2, fgm_40_49: 1, fgm_60p: 1 })).toEqual([20, 20, 40, 60]);
  });

  it("ignores totals and the overlapping 50+ bucket", () => {
    // fgm_50p is fgm_50_59 + fgm_60p; fgm, fgm_yds and fgm_lng are totals (scripts/export_scoring_contract.py).
    expect(fgMadeYardsFromSleeperBuckets({ fgm: 2, fgm_yds: 105, fgm_lng: 53, fgm_50p: 1, fgm_50_59: 1, fgm_30_39: 1 })).toEqual([
      30, 50,
    ]);
  });

  it("is empty with no kicks", () => {
    expect(fgMadeYardsFromSleeperBuckets({})).toEqual([]);
  });

  it("scores a Sleeper kicker the same as his exact distances would under every option", () => {
    // Ka'imi Fairbairn in the live fixture: one FG from 27 yards, 2 PATs.
    const fromBuckets = fgMadeYardsFromSleeperBuckets({ fgm_20_29: 1, fgm_lng: 27, fgm_yds: 27 });
    for (const name of RULES_NAMES) {
      const rules = RULES[name]!;
      expect(scoreKicker({ fg_made_yards: fromBuckets, pat_made: 2 }, rules)).toBe(scoreKicker({ fg_made_yards: [27], pat_made: 2 }, rules));
    }
  });
});
