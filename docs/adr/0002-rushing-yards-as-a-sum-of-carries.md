# RB, WR and TE rushing yards are a sum of carries; QB rushing yards stay a hurdle shifted gamma

Rushing yards were modelled like receiving yards: a point mass at 0 plus a discretised gamma shifted left by a few yards so that negative games are possible. For a low-volume back that shape cannot be right. Its nonzero part needs a coefficient of variation of about 1.2, and a gamma that wide has its density peak at the left edge, at −shift. So 10–16% of the probability went below zero, against 2–3% in seven seasons of nflverse data and 1.5% in the Kalshi Backtest. Changing the shift or the spread cannot fix this: a smaller shift makes a −4 game impossible, and a narrower spread removes the long run that makes these backs' 40-yard games (about 12% of their nonzero games).

Since Sprint 5.6 (S5.10), an RB's, WR's or TE's rushing yards are the sum of N carries. N is negative binomial on the existing carries dispersion relation, and each carry's yards are drawn from that position's per-carry distribution in nflverse play-by-play (`per_carry_yards` in `params/dispersion.json`, built by `scripts/build_params.py`). A fit to a Ladder moves three things: expected carries λ, a scale s that stretches every carry, and a multiplier on the NB size r. Priors keep s and r near the pooled values. A Main Line alone fixes s and r and solves for λ. An Estimated Stat uses λ = mean / yards per carry. P(0) comes out of the model (no carries, or carries that sum to 0), so the RB P(0) override in `market_calibration.json` no longer applies to RBs.

QBs keep the hurdle shifted gamma with shift 7. Kneel-downs and scrambles are not independent, identically distributed carries, and in the research the sum of carries made QB rushing calibration worse (PIT p 0.018 against 0.586).

## Considered Options

- **A smaller shift, or a shift that grows with the mean:** rejected. At a mean of 12 yards, shifts 1, 2, 3 and 5 give 5–14% below zero. Below a shift of about 2 the gamma's shape drops under 1, which puts a spike at the edge and rules out real −3 and −4 games.
- **A narrower gamma at low means:** rejected. It reaches 1.8% below zero, but P(40+) falls from 6.8% to 1.6%, against 11.7% in nflverse.
- **A three-part hurdle** (P(0), a modelled P(< 0) with a fixed negative tail, and a lognormal positive part): rejected. Negative mass was right, but calibration got worse everywhere (RB all-games PIT p 0.003).
- **The existing lognormal nonzero part:** rejected. It still gave 3.3% below zero, with worse PITs (RB all 0.006, mean ≥ 40 yards 0.019).

## Consequences

- The Backtest refits the hurdle gamma on the same Rungs and reports both models side by side, under "Sum of carries vs hurdle gamma", so this decision can be checked on every run.
- Each fit takes one FFT of a 2,048-point grid per objective evaluation, which costs about the same as the gamma.
- The per-carry distribution is pooled per position, and TEs borrow WRs'. Backs who get fewer carries run slightly below the pooled yards per carry and are slightly more often negative than independent carries predict (12.2% vs 9.2% at one carry).
- Rungs at 30+ yards and above for low-volume backs are underpriced by Kalshi (8% priced for 40+, 19% observed). No distribution fixes that, so it is in the backlog.
