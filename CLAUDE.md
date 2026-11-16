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
