"""How much of Sleeper PPR scoring falls outside our SCORING_STATS? (nflverse 2023-2026, QB/RB/WR/TE)

Usage: .venv/bin/python research/scripts/kalshi_ffpts_components.py
"""

import nflreadpy
import polars as pl

ps = nflreadpy.load_player_stats([2023, 2024, 2025, 2026], summary_level="week").filter(
    pl.col("position").is_in(["QB", "RB", "WR", "TE"]) & (pl.col("season_type") == "REG")
)
f = lambda c: pl.col(c).fill_null(0)  # noqa: E731
ps = ps.with_columns(
    st=6 * f("special_teams_tds"),
    frtd=6 * f("fumble_recovery_tds"),
    int_diff=f("passing_interceptions"),  # Sleeper -1 vs our ESPN-ish -2
    fl_other=-2 * (f("fumbles_lost_total") - f("sack_fumbles_lost") - f("rushing_fumbles_lost") - f("receiving_fumbles_lost")),
)
print("player-games:", ps.height)
out = ps.group_by("position").agg(
    pl.len().alias("n"),
    (pl.col("st") > 0).sum().alias("games_with_st_td"),
    pl.col("st").mean().alias("st_td_pts_per_game"),
    (pl.col("frtd") > 0).sum().alias("games_with_fum_rec_td"),
    pl.col("frtd").mean().alias("fum_rec_td_pts_per_game"),
    (pl.col("fl_other") != 0).sum().alias("games_with_other_fumbles_lost"),
).sort("position")
with pl.Config(tbl_cols=-1, tbl_width_chars=200):
    print(out)
