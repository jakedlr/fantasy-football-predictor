/**
 * The Expected Points push's transport checks (site/contracts/push-signature.json, ADR 0006): a
 * capped body read, and the timestamp and HMAC-SHA256 signature. Web Crypto only, so it runs in a
 * Pages Function and in Node's tests alike.
 */

const enc = new TextEncoder();
const TIMESTAMP = /^[0-9]{1,12}$/;
const SIGNATURE = /^sha256=([0-9a-f]{64})$/;

/**
 * The body's bytes, or null if it is over `maxBytes`: refused up front by Content-Length, and
 * again while reading, since the header can be missing or wrong. Reading stops at the cap.
 */
export async function readBody(request: Request, maxBytes: number): Promise<Uint8Array | null> {
  const declared = request.headers.get("Content-Length");
  if (declared !== null && !(/^[0-9]+$/.test(declared) && Number(declared) <= maxBytes)) return null;
  if (request.body === null) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > maxBytes) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const out = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.byteLength;
  }
  return out;
}

export interface PushAuth {
  secret: string;
  /** X-FFP-Timestamp: Unix seconds when signed. */
  timestamp: string | null;
  /** X-FFP-Signature: "sha256=" + lowercase hex. */
  signature: string | null;
  body: Uint8Array;
  now: Date;
  maxSkewSeconds: number;
}

/**
 * True if the timestamp is within maxSkewSeconds of now either way and the signature is the
 * HMAC-SHA256, under the secret's UTF-8 bytes, of `timestamp + "." + body`. crypto.subtle.verify
 * compares in constant time. Callers answer every false the same way, so nothing tells a sender
 * which part failed.
 */
export async function verifyPush(auth: PushAuth): Promise<boolean> {
  const { timestamp, signature } = auth;
  if (timestamp === null || signature === null || !TIMESTAMP.test(timestamp)) return false;
  if (Math.abs(auth.now.getTime() / 1000 - Number(timestamp)) > auth.maxSkewSeconds) return false;
  const hex = SIGNATURE.exec(signature)?.[1];
  if (hex === undefined) return false;
  const prefix = enc.encode(`${timestamp}.`);
  const signed = new Uint8Array(prefix.byteLength + auth.body.byteLength);
  signed.set(prefix, 0);
  signed.set(auth.body, prefix.byteLength);
  return crypto.subtle.verify("HMAC", await hmacKey(auth.secret), fromHex(hex), signed);
}

let cached: { secret: string; key: Promise<CryptoKey> } | null = null;

/** The imported key, kept for the isolate's life while the secret is unchanged. */
function hmacKey(secret: string): Promise<CryptoKey> {
  if (cached?.secret !== secret) {
    const key = crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
    cached = { secret, key };
  }
  return cached.key;
}

function fromHex(hex: string): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
  return out;
}
