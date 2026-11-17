# Custom Scoring Rules

Put a league's rules here as `<name>.json` and pass `--scoring <name>`.
A file here overrides a bundled one of the same name, so to tweak the
default league, copy `src/ffp/rules/espn-league.json` here and edit it.

A file needs a `name` and an `offense` section. Add a `kicker` and a `dst` section to score
kickers and D/ST; copy their layout from `src/ffp/rules/espn-league.json`. Without them, a
kicker or D/ST listed in INPUT.md is skipped.

Tiers (FG distance, Points Allowed, Yards Allowed) are whole-number ranges with `max`
inclusive, each starting one past the previous tier's `max`. Every tier must be listed, even
one worth 0 points, or the file is rejected. Only the last tier may have `"max": null`.
