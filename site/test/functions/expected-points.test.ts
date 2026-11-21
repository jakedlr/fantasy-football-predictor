import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { handlePush, MAX_BODY_BYTES, MAX_PUSH_AGE_MS, MAX_SKEW_SECONDS, onRequest, type PushEnv } from "../../functions/api/expected-points.js";
import { mergeExpected } from "../../src/push/merge.js";
import type { Push, PushPlayer } from "../../src/push/validate.js";
import { KEYS, type ExpectedStore, type IdMapStore } from "../../src/worker/kv.js";
import { FakeKV, readJson } from "../helpers/fakes.js";

const contract = readJson("contracts/push-signature.json");
const SECRET = "a1".repeat(32);
const URL_ = "https://ffp-leaderboard.pages.dev/api/expected-points";
const example = (): Push => readJson("schema/examples/expected-points.json");
// Ten minutes after the example's generatedAt (17:50Z), before its 18:00Z kickoffs.
const NOW = new Date("2026-11-22T17:52:00Z");

function signed(body: string, { now = NOW, secret = SECRET, headers = {} as Record<string, string> } = {}) {
  const timestamp = String(Math.floor(now.getTime() / 1000));
  const signature = `sha256=${createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex")}`;
  return new Request(URL_, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/json", "X-FFP-Timestamp": timestamp, "X-FFP-Signature": signature, ...headers },
  });
}

const env = (kv: FakeKV, secret: string | undefined = SECRET): PushEnv => ({ LEADERBOARD: kv, LEADERBOARD_PUSH_SECRET: secret });
const post = (kv: FakeKV, push: unknown, now = NOW) => handlePush(signed(JSON.stringify(push), { now }), env(kv), now);
const stored = <T>(kv: FakeKV, key: string): T => JSON.parse(kv.data.get(key)!);

afterEach(() => vi.restoreAllMocks());

describe("POST /api/expected-points", () => {
  it("uses the contract's skew and body cap", () => {
    expect(MAX_SKEW_SECONDS).toBe(contract.maxSkewSeconds);
    expect(MAX_BODY_BYTES).toBe(contract.maxBodyBytes);
  });

  it("merges a signed push into KV expected and writes idmap, answering 204", async () => {
    const kv = new FakeKV();
    const push = example();
    const res = await post(kv, push);
    expect(res.status).toBe(204);
    expect(kv.writes.map((w) => w.key)).toEqual([KEYS.idmap, KEYS.expected]);
    expect(stored<ExpectedStore>(kv, KEYS.expected)).toEqual(mergeExpected(null, push));
    expect(stored<IdMapStore>(kv, KEYS.idmap)).toEqual({
      schemaVersion: 1,
      generatedAt: push.generatedAt,
      espnToSleeper: { "4361370": "11635", "4360000": "6789", "4430000": "8150", "4229999": "4227" },
    });
  });

  // Pushes at these times, each sent a minute after it was generated.
  const pushAt = (kv: FakeKV, generatedAt: string, idMap = example().idMap) =>
    post(kv, { ...example(), generatedAt, idMap }, new Date(Date.parse(generatedAt) + 60_000));
  const moved = { ...example().idMap, "9999": "1234" };

  it("writes expected once per push, and idmap only when the map changed", async () => {
    const kv = new FakeKV();
    await pushAt(kv, "2026-11-22T16:00:00Z");
    expect((await pushAt(kv, "2026-11-22T16:30:00Z")).status).toBe(204);
    expect(kv.writes.map((w) => w.key)).toEqual([KEYS.idmap, KEYS.expected, KEYS.expected]);

    expect((await pushAt(kv, "2026-11-22T17:00:00Z", moved)).status).toBe(204);
    expect(kv.writes.map((w) => w.key).slice(3)).toEqual([KEYS.idmap, KEYS.expected]);
    expect(stored<IdMapStore>(kv, KEYS.idmap).espnToSleeper["1234"]).toBe("9999");
  });

  it("writes a changed idmap at most once an hour (KV write budget), catching up on a later push", async () => {
    const kv = new FakeKV();
    await pushAt(kv, "2026-11-22T16:00:00Z");
    await pushAt(kv, "2026-11-22T16:30:00Z", moved);
    expect(kv.writes.map((w) => w.key)).toEqual([KEYS.idmap, KEYS.expected, KEYS.expected]);
    await pushAt(kv, "2026-11-22T17:00:00Z", moved);
    expect(kv.writes.map((w) => w.key).slice(3)).toEqual([KEYS.idmap, KEYS.expected]);
    expect(stored<IdMapStore>(kv, KEYS.idmap).generatedAt).toBe("2026-11-22T17:00:00Z");
  });

  it("answers 429 to a push under 5 minutes after the stored one (KV write budget), writing nothing", async () => {
    const kv = new FakeKV();
    await pushAt(kv, "2026-11-22T16:00:00Z");
    const writes = kv.writes.length;
    const res = await pushAt(kv, "2026-11-22T16:04:59Z");
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("1");
    expect(kv.writes.length).toBe(writes);
    expect((await pushAt(kv, "2026-11-22T16:05:00Z")).status).toBe(204);
  });

  it("answers 400 to a push generated over 15 minutes before the server's now, writing nothing", async () => {
    const kv = new FakeKV();
    const now = new Date(Date.parse("2026-11-22T17:50:00Z") + MAX_PUSH_AGE_MS + 1000);
    const res = await post(kv, example(), now);
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toMatch(/15 minutes/);
    expect(kv.writes).toEqual([]);
    expect((await post(kv, example(), new Date(Date.parse("2026-11-22T17:50:00Z") + MAX_PUSH_AGE_MS))).status).toBe(204);
  });

  it("refuses back-dated pushes after a quiet gap, so the 5-minute spacing runs on the server's clock", async () => {
    const kv = new FakeKV();
    await pushAt(kv, "2026-11-22T10:00:00Z");
    const writes = kv.writes.length;
    // Six hours later, a sender stamps pushes 10:05, 10:10, … to catch up: each is refused.
    const now = new Date("2026-11-22T16:00:00Z");
    for (const at of ["2026-11-22T10:05:00Z", "2026-11-22T12:00:00Z", "2026-11-22T15:40:00Z"]) {
      expect((await post(kv, { ...example(), generatedAt: at }, now)).status).toBe(400);
    }
    expect(kv.writes.length).toBe(writes);
    expect((await post(kv, { ...example(), generatedAt: "2026-11-22T15:50:00Z" }, now)).status).toBe(204);
  });

  describe("a push for a later NFL Week than KV scoreboard's", () => {
    const board = (season: number, seasonType: "regular" | "postseason", number: number) =>
      JSON.stringify({ week: { season, seasonType, number, start: "", end: "", firstKickoff: "", lastKickoff: "" }, games: [] });

    // The example is 2026 regular week 12.
    it.each([
      ["a later week", board(2026, "regular", 13)],
      ["the postseason", board(2026, "postseason", 1)],
      ["a later season", board(2027, "regular", 1)],
    ])("accepts a push for an earlier week than the scoreboard's (%s)", async (_what, b) => {
      const kv = new FakeKV({ [KEYS.scoreboard]: b });
      expect((await post(kv, example())).status).toBe(204);
    });

    it.each<[string, Partial<Push>, string]>([
      ["a later week", { week: 13 }, board(2026, "regular", 12)],
      ["the postseason while the scoreboard is on the regular season", { seasonType: "postseason", week: 1 }, board(2026, "regular", 18)],
      ["a later season", { season: 2027, week: 1 }, board(2026, "postseason", 4)],
    ])("answers 409 to %s, leaving the current week's expected alone", async (_what, change, b) => {
      const kv = new FakeKV({ [KEYS.scoreboard]: b });
      await post(kv, example());
      const before = kv.data.get(KEYS.expected);
      const writes = kv.writes.length;
      const res = await post(kv, { ...example(), ...change, generatedAt: "2026-11-22T17:55:00Z" }, new Date("2026-11-22T17:56:00Z"));
      expect(res.status).toBe(409);
      expect(((await res.json()) as { error: string }).error).toMatch(/scoreboard/);
      expect(kv.writes.length).toBe(writes);
      expect(kv.data.get(KEYS.expected)).toBe(before);
    });

    it("accepts it once the scoreboard has rolled to that week", async () => {
      const kv = new FakeKV({ [KEYS.scoreboard]: board(2026, "regular", 12) });
      expect((await post(kv, example())).status).toBe(204);
    });

    it("accepts any week while there is no scoreboard in KV", async () => {
      const kv = new FakeKV();
      expect((await post(kv, { ...example(), week: 18 })).status).toBe(204);
    });
  });

  it("merges with the stored week: a player whose Game has kicked off stays frozen", async () => {
    const kv = new FakeKV();
    await post(kv, example());
    const later = example();
    later.generatedAt = "2026-11-22T18:30:00Z";
    later.players = [];
    const now = new Date("2026-11-22T18:31:00Z");
    expect((await post(kv, later, now)).status).toBe(204);
    const store = stored<ExpectedStore>(kv, KEYS.expected);
    expect(store.generatedAt).toBe("2026-11-22T18:30:00Z");
    expect(Object.keys(store.players).sort()).toEqual(["dst:BAL", "sleeper:11635", "sleeper:6789"]);
  });

  it("refuses a push older than the stored one with 409, so a delayed retry can't roll back", async () => {
    const kv = new FakeKV();
    await post(kv, { ...example(), generatedAt: "2026-11-22T17:51:00Z" });
    const writes = kv.writes.length;
    const res = await post(kv, example());
    expect(res.status).toBe(409);
    expect(kv.writes.length).toBe(writes);
  });

  it("answers 204 without writing to a repeat of the push already stored", async () => {
    const kv = new FakeKV();
    await post(kv, example());
    const writes = kv.writes.length;
    expect((await post(kv, example())).status).toBe(204);
    expect(kv.writes.length).toBe(writes);
  });

  it("replaces a stored store it can't read", async () => {
    const kv = new FakeKV({ [KEYS.expected]: "not json", [KEYS.idmap]: "{" });
    vi.spyOn(console, "error").mockImplementation(() => {});
    expect((await post(kv, example())).status).toBe(204);
    expect(stored<ExpectedStore>(kv, KEYS.expected)).toEqual(mergeExpected(null, example()));
  });

  describe("authentication", () => {
    const body = JSON.stringify(example());
    const ts = String(Math.floor(NOW.getTime() / 1000));

    it.each<[string, Request]>([
      ["another secret", signed(body, { secret: "b2".repeat(32) })],
      ["a timestamp over 5 minutes old", signed(body, { now: new Date(NOW.getTime() - (MAX_SKEW_SECONDS + 1) * 1000) })],
      ["a timestamp over 5 minutes ahead", signed(body, { now: new Date(NOW.getTime() + (MAX_SKEW_SECONDS + 1) * 1000) })],
      ["no signature", new Request(URL_, { method: "POST", body, headers: { "X-FFP-Timestamp": ts } })],
      ["no headers", new Request(URL_, { method: "POST", body })],
      ["a signature over another body", new Request(URL_, { method: "POST", body: `${body} `, headers: Object.fromEntries(signed(body).headers) })],
    ])("answers 401, the same each time, to %s", async (_what, request) => {
      const kv = new FakeKV();
      const res = await handlePush(request, env(kv), NOW);
      expect(res.status).toBe(401);
      expect(await res.json()).toEqual({ error: "unauthorized" });
      expect(kv.writes).toEqual([]);
    });

    it("answers 500 and logs when the secret isn't set, never accepting the push", async () => {
      const error = vi.spyOn(console, "error").mockImplementation(() => {});
      for (const secret of [undefined, ""]) {
        const kv = new FakeKV();
        const res = await handlePush(signed(body, { secret: "" }), { LEADERBOARD: kv, LEADERBOARD_PUSH_SECRET: secret }, NOW);
        expect(res.status).toBe(500);
        expect(kv.writes).toEqual([]);
      }
      expect(error).toHaveBeenCalled();
      expect(String(error.mock.calls[0])).toMatch(/LEADERBOARD_PUSH_SECRET/);
    });

    // The header checks are cheap; reading up to 1 MB isn't, so they come first.
    it.each<[string, Record<string, string>]>([
      ["no headers", {}],
      ["a malformed timestamp", { "X-FFP-Timestamp": "soon", "X-FFP-Signature": `sha256=${"0".repeat(64)}` }],
      ["a stale timestamp", { "X-FFP-Timestamp": String(Math.floor(NOW.getTime() / 1000) - MAX_SKEW_SECONDS - 1), "X-FFP-Signature": `sha256=${"0".repeat(64)}` }],
      ["a malformed signature", { "X-FFP-Timestamp": ts, "X-FFP-Signature": "sha256=XYZ" }],
    ])("answers 401 to %s without reading the body", async (_what, headers) => {
      const request = new Request(URL_, { method: "POST", body: "x".repeat(MAX_BODY_BYTES + 10), headers });
      const res = await handlePush(request, env(new FakeKV()), NOW);
      expect(res.status).toBe(401);
      expect(request.bodyUsed).toBe(false);
    });
  });

  describe("size", () => {
    it("answers 413 to a Content-Length over the cap", async () => {
      const kv = new FakeKV();
      const request = signed("{}", { headers: { "Content-Length": String(MAX_BODY_BYTES + 1) } });
      expect((await handlePush(request, env(kv), NOW)).status).toBe(413);
    });

    it("answers 413 to a body over the cap, whatever its headers say", async () => {
      const kv = new FakeKV();
      const big = JSON.stringify({ ...example(), pad: "x".repeat(MAX_BODY_BYTES) });
      const res = await handlePush(signed(big), env(kv), NOW);
      expect(res.status).toBe(413);
    });
  });

  describe("content", () => {
    it.each<[string, string, RegExp]>([
      ["invalid JSON", "{not json", /JSON/],
      ["a push failing the schema", JSON.stringify({ ...example(), week: 0 }), /week/],
      ["a generatedAt in the future", JSON.stringify({ ...example(), generatedAt: "2026-11-22T18:30:00Z" }), /future/],
    ])("answers 400 with a short reason to %s", async (_what, body, reason) => {
      const kv = new FakeKV();
      const res = await handlePush(signed(body), env(kv), NOW);
      expect(res.status).toBe(400);
      const { error } = (await res.json()) as { error: string };
      expect(error).toMatch(reason);
      expect(error.length).toBeLessThanOrEqual(240);
      expect(kv.writes).toEqual([]);
    });

    it("answers 400 to a body that isn't UTF-8", async () => {
      const kv = new FakeKV();
      const bytes = new Uint8Array([0x7b, 0xff, 0x7d]);
      const timestamp = String(Math.floor(NOW.getTime() / 1000));
      const signature = `sha256=${createHmac("sha256", SECRET).update(Buffer.concat([Buffer.from(`${timestamp}.`), bytes])).digest("hex")}`;
      const request = new Request(URL_, { method: "POST", body: bytes, headers: { "X-FFP-Timestamp": timestamp, "X-FFP-Signature": signature } });
      expect((await handlePush(request, env(kv), NOW)).status).toBe(400);
    });
  });

  it.each(["GET", "PUT", "DELETE"])("answers 405 to %s, naming POST", async (method) => {
    const res = await onRequest({ request: new Request(URL_, { method }), env: env(new FakeKV()) });
    expect(res.status).toBe(405);
    expect(res.headers.get("allow")).toBe("POST");
  });

  it("uses the real clock through onRequest", async () => {
    const kv = new FakeKV();
    const push = { ...example(), generatedAt: new Date(Math.floor(Date.now() / 1000) * 1000).toISOString().replace(".000Z", "Z") };
    const res = await onRequest({ request: signed(JSON.stringify(push), { now: new Date() }), env: env(kv) });
    expect(res.status).toBe(204);
  });
});

describe("a full-slate push", () => {
  // Players with 10 stat means each and a 2,000-entry id map. A full week's slate is about
  // 300-500 (props for 12-15 players a Game, plus every K and D/ST); 1,000 is the bound checked.
  const stats = ["passing_yards", "passing_tds", "passing_interceptions", "rushing_yards", "rushing_tds", "receptions", "receiving_yards", "receiving_tds", "fumbles_lost", "receiving_2pt"];
  function bigPush(generatedAt: string, size: number): Push {
    const players: PushPlayer[] = [];
    for (let i = 0; i < size; i++) {
      players.push({
        sleeperId: String(10000 + i),
        gsisId: `00-00${String(30000 + i)}`,
        espnId: String(4000000 + i),
        name: `Player Number ${i}`,
        team: ["BAL", "CIN", "KC", "LV", "NYJ", "SF"][i % 6]!,
        position: (["QB", "RB", "WR", "TE"] as const)[i % 4]!,
        kickoff: i % 2 ? "2026-11-22T18:00:00Z" : "2026-11-22T21:25:00Z",
        statMeans: Object.fromEntries(stats.map((s, j) => [s, (i * 7 + j * 13) % 97 / 3])),
        expected: { ppr: 12.34, "half-ppr": 11.2, standard: 10.05, "espn-league": 12.4 },
      });
    }
    const idMap: Record<string, string> = {};
    for (let i = 0; i < 2000; i++) idMap[String(10000 + i)] = String(4000000 + i);
    return { ...example(), generatedAt, players, idMap };
  }

  it.each([500, 1000])("of %i players stays under the 10 ms CPU limit (wall clock on Node, printed)", async (size) => {
    const kv = new FakeKV();
    const first = JSON.stringify(bigPush("2026-11-22T17:30:00Z", size));
    const later = JSON.stringify(bigPush("2026-11-22T17:51:00Z", size));
    expect(first.length).toBeLessThan(MAX_BODY_BYTES);
    expect((await handlePush(signed(first, { now: new Date("2026-11-22T17:31:00Z") }), env(kv), new Date("2026-11-22T17:31:00Z"))).status).toBe(204);

    // Warm runs, as an isolate serving repeat pushes would be; each merges into a full stored week.
    const times: number[] = [];
    for (let run = 0; run < 15; run++) {
      const store = kv.data.get(KEYS.expected)!;
      const fresh = new FakeKV({ [KEYS.expected]: store, [KEYS.idmap]: kv.data.get(KEYS.idmap)! });
      const request = signed(later);
      const start = performance.now();
      const res = await handlePush(request, env(fresh), NOW);
      times.push(performance.now() - start);
      expect(res.status).toBe(204);
    }
    times.sort((a, b) => a - b);
    const median = times[Math.floor(times.length / 2)]!;
    console.log(`full-slate push of ${size}: ${(first.length / 1024).toFixed(0)} KB, median ${median.toFixed(2)} ms, min ${times[0]!.toFixed(2)} ms (wall clock on Node)`);
    // Wall clock on a shared CI runner includes noise; the bound only catches a gross regression.
    expect(median).toBeLessThan(50);
  });
});
