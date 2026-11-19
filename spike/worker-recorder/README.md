# Worker recorder (throwaway, S9.1 Live test)

A Cloudflare Worker that runs every minute (cron `* * * * *`) and fetches the same three URLs as `scripts/record_live.py`: the ESPN scoreboard, the ESPN summary for one Game, and Sleeper's weekly stats. It records only between `RECORD_FROM` and `RECORD_UNTIL` in `wrangler.toml`. Each minute it stores three manifest lines in KV: status, latency, size, game clock and a stats hash per source. It keeps no response bodies. `GET /dump` returns every line as JSONL, which `scripts/analyze_live_spike.py` reads with the home machine's manifest. Results go in `research/leaderboard-live-spike.md`.

The point is the vantage point: Cloudflare's egress IPs are shared datacenter IPs, and ESPN's Akamai has been reported to answer those with 403s. Delete the Worker after the Game.

Notes:

- **CPU.** The free plan allows 10 ms of CPU per run. Only the scoreboard (about 240 KB) is parsed. The summary's box score is sliced out of the raw text and hashed, and plays are counted by their `"sequenceNumber"` keys. Sleeper's raw text is hashed whole. So stats hashes differ from the Python recorder's; the analyzer only compares hashes within one stream.
- **KV.** One write a minute inside the window (300 for the 5-hour window); the free plan allows 1,000 writes a day. A blocked source (403/429) backs off 1, 2, 4, ... up to 16 minutes, with its state in KV.
- **`bytes`** is the body's length in characters (nearly all ASCII), not exact bytes.
- **`/dump` is public** while the Worker exists. It holds only status codes, timings and hashes.
- **Tests.** `npm test` runs a Vitest for the pure parts (URLs, extraction, manifest lines, time window). `index.ts` (KV and fetch) has no tests: it is throwaway. It was run once with `wrangler dev --test-scheduled`, and every source answered 200.

## Deploy (before kickoff)

Needs Node 20+ and a free Cloudflare account. Run from this folder:

```bash
cd spike/worker-recorder
npm install
npx wrangler login                                  # opens the browser; approve
npx wrangler kv namespace create ffp-live-spike     # prints: id = "<32 hex characters>"
sed -i 's/REPLACE_WITH_KV_NAMESPACE_ID/<the id>/' wrangler.toml
npx wrangler deploy                                 # prints https://ffp-live-spike.<your-subdomain>.workers.dev
```

If `wrangler deploy` asks you to register a `workers.dev` subdomain, accept; any name works.

Check the window in `wrangler.toml`. As committed it covers Thursday Night Football, NFL Week 11 (IND @ HOU, ESPN event `401873066`, kickoff 2026-11-20 01:15 UTC = 8:15 PM ET): `RECORD_FROM = "2026-11-20T00:45:00Z"`, `RECORD_UNTIL = "2026-11-20T05:45:00Z"`. Times are UTC.

## During the Game

```bash
npx wrangler tail ffp-live-spike                                    # live logs: one run a minute
curl -s https://ffp-live-spike.<your-subdomain>.workers.dev/dump | tail -3
```

Before `RECORD_FROM`, `/dump` is empty. A line with `"status": 403` or `429` is the blocking we are testing for. Leave it running.

## After the Game

From the repo root:

```bash
curl -s https://ffp-live-spike.<your-subdomain>.workers.dev/dump > snapshots/live-spike/manifest-worker.jsonl
python scripts/analyze_live_spike.py snapshots/live-spike
```

## Remove

```bash
cd spike/worker-recorder
npx wrangler delete                                       # deletes the Worker and its cron
npx wrangler kv namespace delete --namespace-id <the id>  # deletes the stored records
```

Keep `manifest-worker.jsonl` first: deleting the namespace deletes the records. Then revert the id in `wrangler.toml` (`git checkout wrangler.toml`).
