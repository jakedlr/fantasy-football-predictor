import { createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";
import { readBody, verifyPush } from "../../src/push/verify.js";
import { readJson } from "../helpers/fakes.js";

const contract = readJson("contracts/push-signature.json");
const { vector } = contract;
const enc = new TextEncoder();
const at = (unixSeconds: number) => new Date(unixSeconds * 1000);
const signedAt = Number(vector.timestamp);

function sign(secret: string, timestamp: string, body: string) {
  return `sha256=${createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex")}`;
}

interface Over {
  timestamp?: string | null;
  signature?: string | null;
  body?: string;
  secret?: string;
  now?: Date;
}

function check(over: Over = {}) {
  return verifyPush({
    secret: over.secret ?? vector.secret,
    timestamp: over.timestamp === undefined ? vector.timestamp : over.timestamp,
    signature: over.signature === undefined ? vector.signature : over.signature,
    body: enc.encode(over.body ?? vector.body),
    now: over.now ?? at(signedAt),
    maxSkewSeconds: contract.maxSkewSeconds,
  });
}

describe("verifyPush", () => {
  it("accepts the contract vector", async () => {
    expect(await check()).toBe(true);
  });

  it.each<[string, Over]>([
    ["a changed body", { body: vector.body.replace("17:20", "17:21") }],
    ["another secret", { secret: "f".repeat(64) }],
    ["a changed timestamp", { timestamp: String(signedAt + 1), now: at(signedAt + 1) }],
    ["a flipped signature digit", { signature: vector.signature.replace(/.$/, (c: string) => (c === "0" ? "1" : "0")) }],
    ["an uppercase signature", { signature: vector.signature.toUpperCase() }],
    ["no sha256= prefix", { signature: vector.signature.slice("sha256=".length) }],
    ["a short signature", { signature: vector.signature.slice(0, -2) }],
    ["no signature", { signature: null }],
    ["no timestamp", { timestamp: null }],
    ["a non-integer timestamp", { timestamp: `${vector.timestamp}.0` }],
    ["a timestamp in milliseconds", { timestamp: `${vector.timestamp}000` }],
  ])("rejects %s", async (_what, over) => {
    expect(await check(over)).toBe(false);
  });

  it("accepts a timestamp up to maxSkewSeconds either way of now, and not beyond", async () => {
    const skew = contract.maxSkewSeconds;
    expect(await check({ now: at(signedAt + skew) })).toBe(true);
    expect(await check({ now: at(signedAt - skew) })).toBe(true);
    expect(await check({ now: at(signedAt + skew + 1) })).toBe(false);
    expect(await check({ now: at(signedAt - skew - 1) })).toBe(false);
  });

  it("signs the body's UTF-8 bytes, so non-ASCII bodies verify", async () => {
    const body = '{"name":"Ja\'Marr Chase — Bengals ñ"}';
    expect(await check({ body, signature: sign(vector.secret, vector.timestamp, body) })).toBe(true);
  });
});

function streamed(chunks: string[], headers: Record<string, string> = {}) {
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      for (const chunk of chunks) c.enqueue(enc.encode(chunk));
      c.close();
    },
  });
  return new Request("https://x.test/api/expected-points", { method: "POST", body: stream, headers, duplex: "half" } as RequestInit);
}

describe("readBody", () => {
  it("returns the body's bytes when within the cap", async () => {
    const body = await readBody(new Request("https://x.test/", { method: "POST", body: "hello" }), 5);
    expect(new TextDecoder().decode(body!)).toBe("hello");
  });

  it("refuses a Content-Length over the cap", async () => {
    expect(await readBody(streamed(["hi"], { "Content-Length": "6" }), 5)).toBeNull();
  });

  it("refuses a body over the cap when Content-Length is missing or too small", async () => {
    expect(await readBody(streamed(["abc", "def"]), 5)).toBeNull();
    expect(await readBody(streamed(["abc", "def"], { "Content-Length": "3" }), 5)).toBeNull();
  });

  it("reads a body sent in several chunks", async () => {
    const body = await readBody(streamed(["ab", "cd", "e"]), 5);
    expect(new TextDecoder().decode(body!)).toBe("abcde");
  });

  it("treats no body as empty", async () => {
    const body = await readBody(new Request("https://x.test/", { method: "POST" }), 5);
    expect(body?.byteLength).toBe(0);
  });
});
