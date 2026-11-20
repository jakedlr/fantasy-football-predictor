/**
 * The Leaderboard's cron Worker (ADR 0006): one script, three cron triggers, dispatched on
 * `controller.cron`. The schedules must match worker/wrangler.toml (a test checks).
 */
import type { Env } from "./kv.js";
import { runEspn, runScoreboard, runStats, type Outcome, type RunDeps } from "./refresh.js";

/** Minutes 2, 7, 12, …: the scoreboard is fresh, 2–3 minutes old, when the stats run reads it. */
export const SCOREBOARD_CRON = "2-59/5 * * * *";
export const STATS_CRON = "*/5 * * * *";
/**
 * ESPN's projections change slowly: one position group per run, each group hourly (espn.ts
 * `groupAt`). Minutes 9, 29 and 49 are free of the other two runs.
 */
export const ESPN_CRON = "9,29,49 * * * *";

export async function runCron(cron: string, deps: RunDeps): Promise<Outcome> {
  switch (cron) {
    case SCOREBOARD_CRON:
      return runScoreboard(deps);
    case STATS_CRON:
      return runStats(deps);
    case ESPN_CRON:
      return runEspn(deps);
    default:
      throw new Error(`unknown cron ${JSON.stringify(cron)}`);
  }
}

interface ScheduledController {
  cron: string;
  scheduledTime: number;
}

export default {
  /** A failed run has kept the last good data; throwing marks it failed in Cloudflare's cron log. */
  async scheduled(controller: ScheduledController, env: Env, _ctx: unknown): Promise<void> {
    const outcome = await runCron(controller.cron, {
      // Bound, because Workers throw "Illegal invocation" on a detached fetch.
      fetch: (input, init) => fetch(input, init),
      kv: env.LEADERBOARD,
      now: () => new Date(),
      log: (message) => console.error(message),
    });
    if (outcome.result === "failed") throw new Error(outcome.error);
    console.log(`${controller.cron}: ${outcome.result}`);
  },
};
