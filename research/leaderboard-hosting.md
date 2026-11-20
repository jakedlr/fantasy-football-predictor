# Hosting a live leaderboard: free scheduled refresh for a static Cloudflare Pages site

Researched 2026-11-18. Question: how to refresh a public one-page NFL fantasy leaderboard about every 5 minutes, for free, when (a) live player stats come from free JSON APIs (ESPN, Sleeper) and (b) the `ffp` model needs DraftKings/FanDuel prices that only answer from a US residential-type connection.

## TL;DR

- **Don't redeploy Pages to refresh data.** The free plan allows 500 builds a month. A deploy every 5 minutes is 288 a day, about 8,640 a month, so the quota runs out in under two days.
- **Live stats every 5 minutes: a Workers Cron Trigger is viable for free.** It fetches ESPN or Sleeper, writes one JSON blob to KV or D1, and the page reads it. That's 288 runs and at most 288 KV writes a day, against limits of 5 cron triggers and 1,000 KV writes a day. The tight limit is **10 ms of CPU per run** on the free plan, so the Worker must stay a thin fetch, score and store job.
- **The `ffp` model can't run in Cloudflare or GitHub Actions.** The sportsbooks block datacenter IPs. Even apart from that, numpy and scipy do exist in Pyodide, but a model that takes seconds per player will never fit in 10 ms of CPU (or the $5 plan's budgets without real work). It has to run on the home machine, which pushes its output to Cloudflare.
- **Recommendation: a hybrid.** A Worker cron (`*/5`) refreshes live stats. A home Docker job runs `ffp`, at the same 5-minute cadence or slower, and `PUT`s projections to KV through the Cloudflare API. The Worker merges both into `leaderboard.json`. The page polls, shows "Updated 1:35 PM EST · 2 min ago", and flags stale data.
- **GitHub Actions is the weakest choice for a true 5-minute refresh.** `*/5` is its floor, and scheduled runs start late (3 to 16 minutes was observed) or get dropped. Committing JSON triggers a Pages build each time. Scheduled workflows in public repos switch off after 60 days without activity. Private repos would burn through the free minutes.

## 1. Platform limits (official docs)

### Cloudflare (free plan)

| Item | Free limit | Source |
|---|---|---|
| Pages builds | **500 / month**, 1 concurrent, 20 min timeout | [Pages limits](https://developers.cloudflare.com/pages/platform/limits/) |
| Pages Functions | Count as Workers requests (100k/day shared) | [Pages limits](https://developers.cloudflare.com/pages/platform/limits/) |
| Static asset requests | "free and unlimited" | [Static assets billing](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/) |
| Workers requests | 100,000 / day (resets 00:00 UTC; Error 1027 beyond) | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| CPU per invocation | **10 ms**, for HTTP requests and Cron Triggers alike ("occasional overrun tolerated") | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Cron wall-clock | 15 min (network waits don't count as CPU) | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Subrequests | 50 per invocation (1,000 to Cloudflare services) | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| Cron Triggers | 5 per account. Cron syntax down to every minute, in UTC. Changes take up to 15 min to propagate, and runs can be missed meanwhile | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/) |
| Memory | 128 MB | [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) |
| KV | 100k reads/day, **1,000 writes/day**, 1 write/s per key, 25 MiB per value, min `cacheTtl` 30 s | [KV limits](https://developers.cloudflare.com/kv/platform/limits/) |
| D1 | 5M rows read/day, 100k rows written/day, 500 MB per DB | [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/), [D1 limits](https://developers.cloudflare.com/d1/platform/limits/) |
| R2 | 10 GB-month, 1M Class A ops/month (PutObject is Class A), 10M Class B, free egress | [R2 pricing](https://developers.cloudflare.com/r2/pricing/) |
| Workers Paid | $5/month: 10M requests, 30M CPU-ms, 1M KV writes/month | [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/) |

**Python Workers (Pyodide).** Cloudflare says Python Workers support "packages that are included in Pyodide" ([Python packages](https://developers.cloudflare.com/workers/languages/python/packages/)), and Pyodide's list includes numpy 2.4.6 and scipy 1.18.0 ([Pyodide packages](https://pyodide.org/en/stable/usage/packages-in-pyodide.html)). Cloudflare also says "WebAssembly support for Python packages is still in early stages." That doesn't make the model portable:

1. The free plan's 10 ms CPU limit is about three orders of magnitude below "seconds per player".
2. Even on the paid plan, the model's inputs come from sportsbooks that refuse datacenter traffic, and a Worker's egress is a Cloudflare datacenter IP.

**Verdict:** Python Workers aren't an option for `ffp`.

**Deploy hooks.** Every POST to a Pages deploy hook triggers a full build, and those builds count against the 500 a month ([Deploy hooks](https://developers.cloudflare.com/pages/configuration/deploy-hooks/), [Pages limits](https://developers.cloudflare.com/pages/platform/limits/)). Scheduled rebuilds, as in [flaviocopes' pattern](https://flaviocopes.com/scheduled-cloudflare-pages-rebuilds.md), suit daily content. They don't suit 5-minute data.

### GitHub Actions

| Item | Fact | Source |
|---|---|---|
| Min cron interval | "once every 5 minutes" | [Events: schedule](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule) |
| Delays | "can be delayed during periods of high loads… High load times include the start of every hour" | same |
| Branch / TZ | Default branch only. UTC unless an IANA timezone is set | same |
| Inactivity | Public repos: scheduled workflows "automatically disabled when no repository activity has occurred in 60 days" | same |
| Cost | Standard runners are free in public repos. Private repos on GitHub Free get a small included allowance, which the [billing page](https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions) states as a dollar amount; roughly 2,000 Linux minutes is my conversion, not a doc figure. `*/5` is at least 8,640 job-minutes a month, so a private repo isn't viable | billing page |
| External trigger | `repository_dispatch` / `workflow_dispatch` can be fired through the REST API (for example from a Worker cron, to get punctual starts) | [Events: repository_dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#repository_dispatch) |

## 2. What the example repositories show

I examined each repository with its own subagent, read-only.

1. **[simonw/git-scraper-template](https://github.com/simonw/git-scraper-template)** ([post](https://simonwillison.net/2025/Feb/26/git-scraper-template/)): the canonical "git scraping" workflow ([scrape.yml](https://github.com/simonw/git-scraper-template/blob/main/.github/workflows/scrape.yml)).
   - It runs daily (`23 6 * * *`) with `permissions: contents: write`.
   - It commits only on change: `git commit -m "$(date -u)" || exit 0; git pull --rebase; git push`.
   - It pretty-prints JSON with `jq` so the diffs are readable.
   - It has no serving step: consumers read the history or raw.githubusercontent URLs.
   - It has no `concurrency:` or `timeout-minutes`, and you'd want both at `*/5`.
2. **[simonw/ca-fires-history](https://github.com/simonw/ca-fires-history)**: the original git scraper ([2020 post](https://simonwillison.net/2020/Oct/9/git-scraping/)). It's the best real-world evidence on Actions reliability.
   - The cron is `6,26,46 * * * *`, deliberately away from :00.
   - Commit times show a lag of **3 to 7 minutes** after the cron slot, sometimes **8 to 16 minutes**.
   - The workflow is now `disabled_inactivity` (since 2025-05-21), which is the 60-day rule in action.
   - Its last commit replaced the data with an **empty file** when the upstream endpoint died, because nothing validated the output ([#7](https://github.com/simonw/ca-fires-history/issues/7)). Upstream URL changes forced a rewrite before that ([#5](https://github.com/simonw/ca-fires-history/issues/5)). A volatile timestamp field caused noise commits ([#3](https://github.com/simonw/ca-fires-history/issues/3)).
3. **[eidam/cf-workers-status-page](https://github.com/eidam/cf-workers-status-page)**: Worker cron, one KV key, and an edge-rendered page.
   - [wrangler.toml](https://github.com/eidam/cf-workers-status-page/blob/main/wrangler.toml) ships `* * * * *`, but the README says the free KV quota "is sufficient for 2-minute checks only" ([README](https://github.com/eidam/cf-workers-status-page#workers-kv-free-tier)): 1,440 writes a day is over the 1,000 cap.
   - State was collapsed into one key, for one read and one write per run ([#4](https://github.com/eidam/cf-workers-status-page/issues/4)).
   - KV `list` garbage collection moved from the cron to deploy time ([PR #13](https://github.com/eidam/cf-workers-status-page/pull/13)).
   - The 50-subrequest cap limits fan-out ([#23](https://github.com/eidam/cf-workers-status-page/issues/23)).
   - Cron runs in whichever datacenter Cloudflare picks ([#32](https://github.com/eidam/cf-workers-status-page/issues/32)).
   - A Cloudflare metering bug once sent false "90%/100% of KV limit" emails ([#129](https://github.com/eidam/cf-workers-status-page/issues/129)).
4. **[lyc8503/UptimeFlare](https://github.com/lyc8503/UptimeFlare)**: Worker cron and a Next.js Pages frontend, deployed by Actions and Terraform ([deploy.yml](https://github.com/lyc8503/UptimeFlare/blob/main/.github/workflows/deploy.yml)).
   - It first throttled KV writes, writing only on a status change or after a 3-minute cooldown ([#63](https://github.com/lyc8503/UptimeFlare/issues/63)). A flapping monitor broke that ([#112](https://github.com/lyc8503/UptimeFlare/issues/112)), so the project **migrated from KV to D1**: one key/blob table, 100k writes a day.
   - It hit the **10 ms CPU** limit parsing a roughly 1 MB JSON blob ([#92](https://github.com/lyc8503/UptimeFlare/issues/92)) and with more than 10 monitors ([#172](https://github.com/lyc8503/UptimeFlare/issues/172)). The fix was a compact storage format.
   - Subrequests run out at about 35 monitors ([#158](https://github.com/lyc8503/UptimeFlare/issues/158)).
   - Open issue: crons **silently stopped firing** for some users ([#199](https://github.com/lyc8503/UptimeFlare/issues/199)).
5. **[laraiyeo/laraiyeo.github.io](https://github.com/laraiyeo/laraiyeo.github.io)** ("Live Sports Tracker" on GitHub Pages): a static site whose browser calls ESPN directly ([nfl/live.js](https://github.com/laraiyeo/laraiyeo.github.io/blob/HEAD/nfl/live.js)).
   - It polls every **2 s**, with no pause when the tab is hidden, no backoff and no "last updated".
   - Its own [backend README](https://github.com/laraiyeo/laraiyeo.github.io/blob/HEAD/backend/README.md) criticises this ("~2-5MB per fetch", "no caching") and proposes a caching proxy.
   - A curl check today found:
     - ESPN scoreboard: `access-control-allow-origin: *`, `cache-control: max-age=8`.
     - Sleeper `/v1/state/nfl`: `access-control-allow-origin: *`, `s-maxage=60, stale-while-revalidate=180`.

   So CORS isn't a blocker, but a server-side cache is still kinder to ESPN and keeps scoring in one place.

## 3. Prior art in this workspace: prizepicks-optimizer auto runs

`/home/jakedlr/claude-projects/prizepicks-optimizer` (read only):

- **Hosting.** The app ships as one Docker image: a FastAPI service plus a React SPA, started with `docker compose up -d` and `restart: unless-stopped`, with state on a `/data` volume. It is deliberately not hosted: "the sportsbooks only answer requests from a US connection, so it can't be hosted for you". The README asks for "a US network location where FanDuel and DraftKings load normally (no VPN)". [ADR 0001](../../prizepicks-optimizer/docs/adr/0001-web-app-single-container.md) adds that FanDuel and DraftKings geo-block requests and must be reached from the user's own US connection, and the odds are read from the books' New Jersey hosts.
- **Auto runs** ([ADR 0003](../../prizepicks-optimizer/docs/adr/0003-server-side-auto-runs.md)). The schedule lives in the server process, not a browser tab, because tabs stop when closed or asleep and throttle timers.
  - A `Scheduler` thread in `src/ppopt/runs.py` acts on each quarter mark (`epoch % 900`, so :00/:15/:30/:45) at most once.
  - It acts only within 60 s of the mark and never catches up on marks missed while the app was down.
  - It wakes at least once a minute, so a laptop sleeping or a clock jump doesn't wedge it.
- **Run handling.** One run slot is shared by manual and auto runs: a mark that finds a run going is skipped. The latest report is persisted (`latest.json`).
- **Failures.** A failed board fetch switches auto runs off with a reason. Results are pushed to a phone through ntfy.
- **Page.** It polls status every 5 s while visible and every 30 s while hidden.

**What carries over:** the home-machine half of the recommended design can reuse this pattern almost verbatim: a Docker container, a scheduler on fixed wall-clock marks, skip-if-busy, no catch-up, a persisted last result. The difference is that each run ends with a push to Cloudflare instead of a local report.

## 4. Candidate architectures

Common front end for all of them: a static `index.html` on Pages (asset requests are free and unlimited) that polls one JSON URL.

### A. Worker Cron Trigger, then KV/D1, then a Worker/Pages Function serving JSON (stats only)

- **Flow:**
  1. A `scheduled()` handler on `*/5 * * * *` fetches the ESPN or Sleeper week stats.
  2. It applies league scoring (full PPR, 4-pt pass TD, custom K/DST) in JS.
  3. It writes `leaderboard.json` to one KV key, or a D1 row, **only if its hash changed**.
  4. The `fetch()` handler returns it with `Cache-Control: public, max-age=60` and uses the Cache API so most page views don't touch KV.
- **Cost:** $0. That's 288 cron runs and at most 288 KV writes a day (29% of 1,000; D1's 100k a day leaves huge headroom). Reads are 100k a day: one viewer polling every minute for a 10-hour Sunday is about 600 reads, so cache at the edge.
- **Latency:** about 5 min, plus a few seconds of KV propagation, plus the client poll interval.
- **Setup:** `wrangler.toml` with `[triggers] crons = ["*/5 * * * *"]` and a KV or D1 binding; `wrangler deploy`; serve the page as Worker static assets or from Pages.
- **Failure modes:**
  - **10 ms CPU.** Parsing 14 to 16 ESPN game summaries of hundreds of KB each can blow it (see UptimeFlare #92). Prefer one compact source (Sleeper's weekly stats, or the ESPN scoreboard plus only the in-progress games' box scores) and keep the stored blob small.
  - At most 50 subrequests.
  - Crons silently stopping (UptimeFlare #199).
  - ESPN/Sleeper are undocumented and can change.
- **Limits of this design:** it can't produce `ffp` projections, since there's no sportsbook access and no CPU for the model.
- **Fallback:** the $5 Workers Paid plan lifts CPU to seconds and KV writes to 1M a month.
- **5-minute viability: yes.** It's the most punctual free scheduler here, and the free cron syntax goes down to 1 minute.

### B. GitHub Actions on a schedule (git scraping)

There are two variants:

- **B1:** commit `leaderboard.json`, and Pages rebuilds.
- **B2:** push it with `wrangler kv key put leaderboard --path leaderboard.json --binding LB --remote` ([wrangler KV](https://developers.cloudflare.com/workers/wrangler/commands/kv/)) or to R2, with no rebuild.

The rest:

- **Cost:** $0 in a public repo. In a private repo, `*/5` (at least 8,640 job-minutes a month) far exceeds the free allowance.
- **Latency:** nominally 5 min. In practice expect 3 to 16 min late, with dropped runs at busy times, especially around :00.
- **Setup:** a workflow with `schedule`, `concurrency`, `timeout-minutes`, `CLOUDFLARE_API_TOKEN` as a secret, and an off-minute cron such as `2-59/5 * * * *`.
- **Failure modes:**
  - **B1 needs 8,640 Pages builds a month against 500, so it's not viable.**
  - The 60-day inactivity disable (bot commits may not count).
  - Committing empty or garbage data without validation (ca-fires-history).
  - Repository bloat.
  - Runner IPs are Azure datacenter addresses, so it can't run `ffp` against the books.
- **5-minute viability: no** for B1. **Best-effort only** for B2: it averages perhaps every 5 to 15 minutes, with gaps. It can be made punctual by firing `workflow_dispatch` from a Worker cron, but at that point the Worker could do the job itself.

### C. Home machine runs `ffp` and pushes to Cloudflare (KV/R2 through API or wrangler)

- **Flow:**
  1. A Docker container (or systemd timer) on the home machine, behind its US residential IP, wakes on 5-minute marks, prizepicks-`Scheduler` style.
  2. It runs `ffp` for the leaderboard's players. Sprint 7/8 timing is about 16 to 19 s for a 9-player lineup, so a larger player pool fits easily within 5 minutes.
  3. It validates the output, then uploads it with `PUT https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{ns}/values/{key}` ([KV write API](https://developers.cloudflare.com/api/resources/kv/subresources/namespaces/subresources/values/methods/update/)) using a token scoped to "Workers KV Storage Write" only, or with `wrangler kv key put`/`r2 object put`.
- **Cost:** $0 on Cloudflare (288 writes a day at a 5-minute cadence; R2 would be about 8,640 Class A ops a month out of 1M). The costs are the home machine's electricity and its uptime.
- **Latency:** run time plus upload, about 5 min end to end.
- **Failure modes:**
  - The home machine sleeps, reboots, or the ISP or power goes down: the data goes stale. Mitigate with `restart: unless-stopped`, a `generatedAt` timestamp shown on the page, and a staleness alert (ntfy, as prizepicks does).
  - The books rate-limit (429) or refuse (403): `ffp` already slows down and drops a book.
  - The token leaks: scope it to one namespace.
  - Sharing KV's 1,000 writes a day with A: 288 + 288 = 576, which is fine. D1 is safer.
- **5-minute viability: yes** while the machine is up. The odds themselves rarely need that cadence: props mostly close at kickoff, so a 15 to 30 min `ffp` cadence before games plus 5-min live stats from A is a sensible split.

### D. Cloudflare Tunnel to a server on the home machine

- **Flow:** expose the home FastAPI/JSON endpoint through `cloudflared`, and the Pages site fetches from it.
- **Notes:**
  - Quick Tunnels (`trycloudflare.com`) "are for testing and development", have "no uptime guarantee", and allow 200 in-flight requests ([Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)).
  - Production needs a named tunnel, which in practice means a domain on Cloudflare ([Tunnels overview](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)).
- **Cost:** $0 plus a domain (about $10 a year).
- **Latency:** real-time, but it's limited by home upload bandwidth.
- **Failure modes:** **every page view hits the home machine**, so the public site goes down when the PC does. It also widens the attack surface on the home network.
- **5-minute viability: yes**, but it buys nothing over C and is less robust.

### Comparison

| | A. Worker cron | B. Actions (B2 push) | C. Home push | D. Tunnel |
|---|---|---|---|---|
| Cost | $0 | $0 public / no private | $0 (+ home PC) | $0 + domain |
| Runs `ffp` / reaches books | No | No (datacenter IP) | **Yes** | Yes |
| Live stats | Yes | Yes | Yes | Yes |
| Punctual 5 min? | Yes (rare silent stops) | No: 3 to 16 min late, drops | Yes while PC up | Yes while PC up |
| Survives home PC off | **Yes** | Yes | Shows stale data | **Site down** |
| Main limit | 10 ms CPU, 50 subreq | 60-day disable, cron jitter | Home uptime | Home uptime, exposure |

## 5. Recommendation

**Use A and C together, with Pages (or Worker static assets) for the page.**

1. **Worker** (`*/5` cron plus a fetch handler, KV or preferably D1 single-blob):
   - The cron pulls live stats and recomputes fantasy points with the league's scoring.
   - It merges in the latest `ffp` projections pushed from home.
   - It writes `leaderboard.json` with `generatedAt`, `statsAt` and `projectionsAt`, only on change.
   - It serves the file with `Cache-Control: public, max-age=60, stale-while-revalidate=240`, cached at the edge.
2. **Home container** (prizepicks-style scheduler): runs `ffp` every 5 to 30 minutes on game days, validates the output (non-empty, schema check), and `PUT`s `projections.json` to KV/D1 with a narrowly scoped token. If it's off, the leaderboard still updates live points and labels projections "as of 11:45 AM".
3. **Guard rails:**
   - A `generatedAt` older than 15 min shows a "data delayed" banner.
   - Optionally, the Worker cron pings ntfy when `projectionsAt` goes stale on a game day.
   - Keep the stored JSON small (10 ms CPU).
   - Keep below 50 subrequests a run.
   - If CPU limits bite, the $5 Workers Paid plan is the escape hatch.

Avoid Pages redeploys for data (500 builds a month) and Actions as the primary scheduler.

## 6. Client-side refresh pattern

- **Poll the JSON**, not the page: `fetch('/leaderboard.json', {cache: 'no-cache'})`. That revalidates with the ETag, so an unchanged file is a cheap 304 where the server supports it.
  - Poll every 60 s, or schedule the next poll at `generatedAt + 5 min + 20 s jitter`.
  - Pause on `document.visibilityState === 'hidden'` and fetch immediately on `visibilitychange` back to visible.
  - Back off exponentially on errors and keep the last good data on screen.
- **Cache headers:** for a static file on Pages, use a `_headers` rule (`/leaderboard.json` / `Cache-Control: public, max-age=60`) ([Pages headers](https://developers.cloudflare.com/pages/configuration/headers/)). `_headers` doesn't apply to Function or Worker responses, so set headers in code there. Keep `max-age` at or below the poll interval so viewers are never more than about a minute behind the stored data.
- **"Last updated" in the viewer's timezone:**
  ```js
  const fmt = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
  updatedEl.textContent = fmt.format(new Date(data.generatedAt)); // ISO 8601 UTC from the server
  ```
- **Live "x minutes ago"** with [`Intl.RelativeTimeFormat`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/RelativeTimeFormat), re-rendered every 15 to 30 s:
  ```js
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  const mins = Math.round((Date.parse(data.generatedAt) - Date.now()) / 60000);
  agoEl.textContent = rtf.format(mins, 'minute'); // "2 minutes ago", "now"
  ```
  Use a `<time datetime="…">` element so the ISO time is machine-readable. Show a stale state when the data is more than 15 minutes old.

## Sources

**Cloudflare:**
- https://developers.cloudflare.com/pages/platform/limits/
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developers.cloudflare.com/workers/platform/pricing/
- https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/
- https://developers.cloudflare.com/kv/platform/limits/
- https://developers.cloudflare.com/d1/platform/limits/
- https://developers.cloudflare.com/d1/platform/pricing/
- https://developers.cloudflare.com/r2/pricing/
- https://developers.cloudflare.com/workers/languages/python/
- https://developers.cloudflare.com/workers/languages/python/packages/
- https://developers.cloudflare.com/pages/configuration/deploy-hooks/
- https://developers.cloudflare.com/pages/configuration/headers/
- https://developers.cloudflare.com/workers/wrangler/commands/kv/
- https://developers.cloudflare.com/api/resources/kv/subresources/namespaces/subresources/values/methods/update/
- https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/

**Pyodide:** https://pyodide.org/en/stable/usage/packages-in-pyodide.html

**GitHub:**
- https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#repository_dispatch
- https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions

**MDN:** https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/RelativeTimeFormat

**Repositories and posts:**
- simonw/git-scraper-template:
  - https://github.com/simonw/git-scraper-template
  - https://simonwillison.net/2025/Feb/26/git-scraper-template/
- simonw/ca-fires-history:
  - https://github.com/simonw/ca-fires-history
  - https://simonwillison.net/2020/Oct/9/git-scraping/
  - https://github.com/simonw/ca-fires-history/issues/7
  - https://github.com/simonw/ca-fires-history/issues/5
  - https://github.com/simonw/ca-fires-history/issues/3
- eidam/cf-workers-status-page:
  - https://github.com/eidam/cf-workers-status-page
  - https://github.com/eidam/cf-workers-status-page/issues/4
  - https://github.com/eidam/cf-workers-status-page/pull/13
  - https://github.com/eidam/cf-workers-status-page/issues/23
  - https://github.com/eidam/cf-workers-status-page/issues/32
  - https://github.com/eidam/cf-workers-status-page/issues/129
- lyc8503/UptimeFlare:
  - https://github.com/lyc8503/UptimeFlare
  - https://github.com/lyc8503/UptimeFlare/issues/63
  - https://github.com/lyc8503/UptimeFlare/issues/92
  - https://github.com/lyc8503/UptimeFlare/issues/112
  - https://github.com/lyc8503/UptimeFlare/issues/158
  - https://github.com/lyc8503/UptimeFlare/issues/172
  - https://github.com/lyc8503/UptimeFlare/issues/199
- laraiyeo/laraiyeo.github.io:
  - https://github.com/laraiyeo/laraiyeo.github.io
  - https://github.com/laraiyeo/laraiyeo.github.io/blob/HEAD/backend/README.md
- Scheduled Pages rebuilds: https://flaviocopes.com/scheduled-cloudflare-pages-rebuilds.md
- Local, read only: `/home/jakedlr/claude-projects/prizepicks-optimizer` (README.md, docs/adr/0001, docs/adr/0003, docs/sprints.md, src/ppopt/runs.py, Dockerfile, docker-compose.yml)
