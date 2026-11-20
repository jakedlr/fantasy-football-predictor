import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";

const read = (path: string) => JSON.parse(readFileSync(new URL(`../${path}`, import.meta.url), "utf8"));

function validator(schemaPath: string) {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats.default(ajv);
  return ajv.compile(read(schemaPath));
}

describe("leaderboard.schema.json", () => {
  const validate = validator("schema/leaderboard.schema.json");

  it("accepts the example with every Game Status", () => {
    const example = read("schema/examples/leaderboard.json");
    expect(validate(example), JSON.stringify(validate.errors)).toBe(true);
    expect(new Set(example.games.map((g: { status: string }) => g.status))).toEqual(new Set(["upcoming", "live", "final"]));
  });

  it("rejects a Game Status outside the three", () => {
    const example = read("schema/examples/leaderboard.json");
    example.games[0].status = "in progress";
    expect(validate(example)).toBe(false);
  });

  it("ids players by Sleeper id, or by team for a D/ST", () => {
    const example = read("schema/examples/leaderboard.json");
    const ids = example.players.map((p: { id: string }) => p.id);
    expect(ids.every((id: string) => /^(sleeper:\d+|dst:[A-Z]{2,3})$/.test(id))).toBe(true);
    example.players[0].id = "espn:4361370";
    expect(validate(example)).toBe(false);
  });

  it("rejects an update time without a UTC zone", () => {
    const example = read("schema/examples/leaderboard.json");
    example.updatedAt = "2026-11-19T20:05:00-05:00";
    expect(validate(example)).toBe(false);
  });
});

describe("expected-points.schema.json", () => {
  const validate = validator("schema/expected-points.schema.json");

  it("accepts the example push", () => {
    const example = read("schema/examples/expected-points.json");
    expect(validate(example), JSON.stringify(validate.errors)).toBe(true);
  });

  it("rejects a push player without a Sleeper id", () => {
    const example = read("schema/examples/expected-points.json");
    delete example.players[0].sleeperId;
    expect(validate(example)).toBe(false);
  });

  it("rejects a Scoring Stat mean outside ffp.scoring's keys and Sleeper's FG buckets", () => {
    const example = read("schema/examples/expected-points.json");
    example.players[1].statMeans.fgm_50p = 0.2;
    expect(validate(example)).toBe(false);
  });

  it("requires Expected Points under every Leaderboard Scoring Rules option", () => {
    const example = read("schema/examples/expected-points.json");
    delete example.players[0].expected["espn-league"];
    expect(validate(example)).toBe(false);
  });

  it("requires the Sleeper-to-ESPN id map", () => {
    const example = read("schema/examples/expected-points.json");
    delete example.idMap;
    expect(validate(example)).toBe(false);
  });

  it("rejects a push without Expected Points", () => {
    const example = read("schema/examples/expected-points.json");
    example.players[0].expected = {};
    expect(validate(example)).toBe(false);
  });
});

describe("the two examples", () => {
  it("agree on Expected Points, which are frozen at kickoff", () => {
    const board = read("schema/examples/leaderboard.json");
    const push = read("schema/examples/expected-points.json");
    const byId = new Map<string, Record<string, number>>();
    for (const p of push.players) byId.set(p.position === "D/ST" ? `dst:${p.team}` : `sleeper:${p.sleeperId}`, p.expected);
    let matched = 0;
    for (const player of board.players) {
      const expected = byId.get(player.id);
      if (!expected || player.expectedSource !== "ffp") continue;
      matched += 1;
      for (const [rule, pts] of Object.entries(player.points as Record<string, { expected: number }>)) {
        expect(pts.expected, `${player.id} ${rule}`).toBe(expected[rule]);
      }
    }
    expect(matched).toBeGreaterThan(0);
  });
});

describe("push-signature.json", () => {
  it("is HMAC-SHA256 of timestamp + '.' + body under the secret's UTF-8 bytes", () => {
    const { vector } = read("contracts/push-signature.json");
    const hex = createHmac("sha256", vector.secret).update(`${vector.timestamp}.${vector.body}`).digest("hex");
    expect(`sha256=${hex}`).toBe(vector.signature);
  });
});
