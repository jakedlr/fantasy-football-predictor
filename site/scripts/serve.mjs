#!/usr/bin/env node
/**
 * Local preview of the Leaderboard page: serves site/public and a stub /api/leaderboard.
 *
 *   npm run dev                      # http://localhost:8788, ~700 generated players
 *   PORT=9000 node scripts/serve.mjs example
 *
 * Scenarios (first argument, or switch while running with GET /__scenario/<name>):
 *   sunday    ~700 players, Sprint 10's shape: Expected Points null everywhere (default)
 *   expected  ~700 players with Expected Points and ESPN projections, as later sprints will send
 *   example   site/schema/examples/leaderboard.json
 *   delayed   sunday, last updated 22 minutes ago
 *   empty     Games but no players yet
 *   error     /api/leaderboard answers 503
 *   slow      sunday after a 3 s wait, to see the skeleton
 */
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const root = fileURLToPath(new URL("..", import.meta.url));
const pub = join(root, "public");
const port = Number(process.env.PORT ?? 8788);
let scenario = process.argv[2] ?? "sunday";

// The fixture generator is TypeScript shared with the tests; bundle it in memory.
const out = await build({ entryPoints: [join(root, "test/page/fixture.ts")], bundle: true, format: "esm", platform: "node", write: false });
const { generateLeaderboard } = await import(`data:text/javascript;base64,${Buffer.from(out.outputFiles[0].text).toString("base64")}`);

async function board(name) {
  const now = Date.now();
  switch (name) {
    case "example": return JSON.parse(await readFile(join(root, "schema/examples/leaderboard.json"), "utf8"));
    case "expected": return generateLeaderboard({ now, withExpected: true });
    case "delayed": return generateLeaderboard({ now, ageMinutes: 22 });
    case "empty": return { ...generateLeaderboard({ now }), players: [] };
    default: return generateLeaderboard({ now });
  }
}

const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".map": "application/json", ".woff2": "font/woff2", ".txt": "text/plain" };

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  try {
    if (url.pathname.startsWith("/__scenario/")) {
      scenario = url.pathname.slice("/__scenario/".length);
      res.writeHead(200, { "content-type": "text/plain" }).end(`scenario: ${scenario}\n`);
      return;
    }
    if (url.pathname === "/api/leaderboard") {
      if (scenario === "error") { res.writeHead(503).end(); return; }
      if (scenario === "slow") await new Promise((r) => setTimeout(r, 3000));
      res.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" }).end(JSON.stringify(await board(scenario)));
      return;
    }
    const path = normalize(join(pub, url.pathname === "/" ? "index.html" : decodeURIComponent(url.pathname)));
    if (!path.startsWith(pub)) { res.writeHead(403).end(); return; }
    const body = await readFile(path);
    res.writeHead(200, { "content-type": TYPES[extname(path)] ?? "application/octet-stream" }).end(body);
  } catch {
    res.writeHead(404).end("not found");
  }
}).listen(port, () => console.log(`Leaderboard preview on http://localhost:${port} (scenario: ${scenario})`));
