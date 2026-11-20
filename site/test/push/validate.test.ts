import { Ajv2020 } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { validatePush } from "../../src/push/validate.js";
import { readJson } from "../helpers/fakes.js";

const schema = readJson("schema/expected-points.schema.json");
const example = () => readJson("schema/examples/expected-points.json");

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats.default(ajv);
const ajvValidate = ajv.compile(schema);

type Push = ReturnType<typeof example>;
const cases: [string, (p: Push) => unknown][] = [
  ["the example", (p) => p],
  ["a player without a Sleeper id", (p) => delete p.players[0].sleeperId],
  ["a stat outside the schema's keys", (p) => (p.players[1].statMeans.fgm_50p = 0.2)],
  ["a negative stat mean", (p) => (p.players[0].statMeans.receptions = -1)],
  ["a missing Scoring Rules option", (p) => delete p.players[0].expected["espn-league"]],
  ["an extra Scoring Rules option", (p) => (p.players[0].expected.superflex = 1)],
  ["no idMap", (p) => delete p.idMap],
  ["a non-numeric idMap key", (p) => (p.idMap.abc = "1")],
  ["a generatedAt with an offset", (p) => (p.generatedAt = "2026-11-22T12:50:00-05:00")],
  ["a generatedAt that isn't a date", (p) => (p.generatedAt = "2026-13-45T99:00:00Z")],
  ["a kickoff that isn't a date", (p) => (p.players[0].kickoff = "Sunday Z")],
  ["an unknown position", (p) => (p.players[0].position = "FB")],
  ["schemaVersion 2", (p) => (p.schemaVersion = 2)],
  ["week 0", (p) => (p.week = 0)],
  ["a fractional season", (p) => (p.season = 2026.5)],
  ["an unknown top-level field", (p) => (p.extra = true)],
  ["a lowercase team", (p) => (p.players[0].team = "bal")],
  ["an empty name", (p) => (p.players[0].name = "")],
  ["a string Expected Points", (p) => (p.players[0].expected.ppr = "16.2")],
  ["players as an object", (p) => (p.players = {})],
  ["a null push", () => null],
  ["an array", () => []],
];

describe("validatePush", () => {
  it.each(cases)("agrees with the test suite's Ajv on %s", (_what, change) => {
    const push = example();
    const result = change(push);
    const instance = result === null || Array.isArray(result) ? result : push;
    const ajvSays = ajvValidate(instance);
    const ours = validatePush(instance);
    expect(ours.ok, JSON.stringify(ours)).toBe(ajvSays);
  });

  it("accepts the example and returns it", () => {
    const push = example();
    const result = validatePush(push);
    expect(result).toEqual({ ok: true, push });
  });

  it("gives a short reason naming where the push is wrong", () => {
    const push = example();
    push.players[1].statMeans.fgm_50p = 0.2;
    const result = validatePush(push);
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.reason).toMatch(/\/players\/1\/statMeans/);
    expect(result.reason.length).toBeLessThanOrEqual(200);
  });

  it.each<[string, (p: Push) => unknown, RegExp]>([
    ["two entries for one player", (p) => p.players.push({ ...p.players[0] }), /duplicate player sleeper:11635/],
    ["two entries for one D/ST", (p) => p.players.push({ ...p.players[2] }), /duplicate player dst:BAL/],
    ["a D/ST whose Sleeper id isn't its team", (p) => (p.players[2].sleeperId = "CIN"), /D\/ST/],
    ["a player whose Sleeper id is a team", (p) => (p.players[0].sleeperId = "BAL"), /Sleeper id/],
  ])("rejects %s, which the schema alone allows", (_what, change, reason) => {
    const push = example();
    change(push);
    expect(ajvValidate(push)).toBe(true);
    const result = validatePush(push);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toMatch(reason);
  });
});
