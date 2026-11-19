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

  it("rejects a push without Expected Points", () => {
    const example = read("schema/examples/expected-points.json");
    example.players[0].expected = {};
    expect(validate(example)).toBe(false);
  });
});
