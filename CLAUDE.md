# Project instructions

## Keep README.md current

`README.md` is the user-facing description of what `ffp` does and how to run it. Update it in the same commit as any change that affects:

- CLI commands, options or defaults;
- input or output formats;
- supported positions or stats, model choices, or the assumptions listed in the output;
- install steps, dependencies, or how to run tests and `scripts/build_params.py`;
- project status (sprint finished, PR merged, feature moved to or from the backlog).

Before finishing any task, check whether the README still describes the code accurately. If it doesn't, fix it. A PR that changes any of the above without a README update is incomplete.

## Other docs

- `PLAN.md`: sprint plan, settled decisions and backlog. Update it when a decision or the scope changes.
- `GLOSSARY.md`: domain vocabulary. Use its terms (Score Percentile, Ladder, Rung, Usage Stat, ...) in code, docs and messages.
- `docs/adr/`: record significant architecture decisions there.

## Pull requests

The user doesn't review PRs on this project. For every PR:

1. Spawn one or more reviewer subagents on the PR's diff (correctness, security, docs vs code, the free-tier limits in PLAN.md). Give them the context they need; they start cold.
2. Fix what they find, or say in the PR why a finding was left.
3. Once the review is clean and CI passes, merge it yourself (`gh pr merge <n> --merge`). Post the review summary as a PR comment: GitHub won't let the user's own account approve the PR.
4. Merging to `main` deploys the Leaderboard site (`.github/workflows/deploy.yml`): watch that run and check the site afterwards.
5. Tell the user what merged, what the review found and how the deploy went.
