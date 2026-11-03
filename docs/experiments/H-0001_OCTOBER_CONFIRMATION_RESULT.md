# H-0001: October 2026 confirmation result (v0.2 and v0.3)

Status: **October 2026 opened once on 2026-11-03 under explicit user authorization** (2026-11-03,
"Давай согласен, делай"), after v0.3 was registered (`e7f6fe0`) and frozen (HL-0003, `57839fe`).
Both confirmations ran on unchanged, committed code and configs; no rule, lag, width, threshold
or exclusion was changed after any October value was computed. October is now training
evidence; a future holdout must be registered for any further confirmation.

## Outcome

| Test | Category | Key numbers (October, equal-day Fisher-z mean) |
|---|---|---|
| v0.2 frozen 1 s screen, `h*=1 s`, cap 5 s | **SCREEN_POSITIVE** | r = 0.216 vs one-sided 95 % null threshold 0.0038 (p = 1/2001); 19/19 included days positive; reverse (−1 s) 0.019; all 7 decision conditions true |
| v0.3 multi-scale, k = +1 bar | **SCREEN_POSITIVE** | all four widths pass (5/10/30/60 s); 21 eligible days |
| Combined decision (protocol table) | **PROCEED** | dependence confirmed; propose read-only order-book recording (needs its own authorization) |

### v0.2 details (`confirmation.json` `135a4067…`)

- Included 19 of 21 primary October days; 2026-10-19 and 2026-10-26 excluded by the frozen
  rule `moex_stale_fraction_ge_max` (e.g. 10-19: MOEX stale fraction 0.515 at cap 5 s).
- Data gate passed; strongest-day share 0.071 (< 0.35).
- Controls: circular shift 0.0004, impossible time (next day) 0.0016, day-mismatch threshold
  0.0038 — none reproduces the effect. Caps: 1 s 0.381, 5 s 0.216, 10 s 0.195 (same sign).
- Simultaneous grid: lags above the 95 % max-statistic threshold (0.0048): 1, 2, 5, 10 s
  (r 0.216 / 0.127 / 0.040 / 0.006); 30 s and 60 s at ≈ 0.

### v0.3 details (`confirmation.json` `bc839a7e…`)

| Δ | forward r (Oct) | Sep calibration | T | p_fw | days > 0 | strongest day | reverse r | p_rev | passes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 5 s | 0.291 | 0.292 | 66.5 | 0.0005 | 100 % | 0.064 | −0.006 | 0.48 | yes |
| 10 s | 0.220 | 0.283 | 43.4 | 0.0005 | 95 % | 0.070 | −0.027 | 0.0005 | yes |
| 30 s | 0.136 | 0.161 | 22.8 | 0.0005 | 100 % | 0.078 | +0.009 | 0.36 | yes |
| 60 s | 0.049 | 0.102 | 6.2 | 0.0005 | 86 % | 0.125 | −0.008 | 0.76 | yes |

- p = 1/2001 is the smallest value 2000 permutations allow.
- At 10 s the reverse direction is family-wise significant but **negative** and small
  (|−0.027| ≤ 0.25 × 0.220), so by the frozen rule it does not block; a small negative reverse
  correlation is consistent with MOEX last-trade bounce/mean reversion, not with a common move
  or a clock shift (both would give a positive reverse effect).
- The effect decays with the bar width faster in October than in September (60 s: 0.049 vs
  0.102), consistent with a catch-up of a few to ~20 seconds.

## What this does and does not show

- It confirms, on a month not used for any choice, that **COMEX GCZ6 one-second-to-one-minute
  price moves predict the next-bar move of the MOEX GDZ6 last-trade price**, in the COMEX →
  MOEX direction only.
- It does **not** show that the lag is in MOEX quotes. Prices are last trades; much of the
  apparent lag can be MOEX trades arriving later than quote updates (non-synchronous trading;
  September diagnostic: with fresh prices at both ends the lagged part was ≈ 5 %). COMEX times
  are Databento receive times; the T-Bank clock is undocumented.
- It says nothing about costs or executability. The September paper calculation (≈ 0.5 bp gross
  per trade vs ≥ 2.64 bp taker round trip) stands: taker trading does not cover costs; any
  maker scenario needs order-book data.

## Next stage (requires explicit user authorization)

Read-only forward recording of the GDZ6 order book (and trades) via the T-Bank Invest API with
a read-only token set locally by the user, to measure whether MOEX **quotes** lag COMEX, spread,
depth and passive-fill feasibility. COMEX data for the recorded period would be a new provider
request with its own cost gate.

## Evidence

| Item | SHA-256 / value |
|---|---|
| v0.2 opened marker | `reports/generated/h0001-seconds-v0-2/ledger/999003a3….opened.json`, opened 2026-11-03T07:48:28.367544Z |
| v0.2 confirmation | `reports/generated/h0001-seconds-v0-2/p3-discovery-20261102-v1/confirmation.json` `135a4067f2b4be2b19563c52cb56a37ba0b752133a0f0f9eb794e687e3ee9499` |
| v0.3 calibration | `reports/generated/h0001-multiscale-v0-3/h0001-v03-20261103/calibration.json` `bdd5f382dab74f6de0d5de12a1171910d54f206aaa06be9193bd65badf47158c`, created 2026-11-03T07:47:43.524854Z |
| v0.3 opened marker | `reports/generated/h0001-multiscale-v0-3/ledger/bdd5f382….opened.json`, opened 2026-11-03T07:49:40.299103Z |
| v0.3 confirmation | `reports/generated/h0001-multiscale-v0-3/h0001-v03-20261103/confirmation.json` `bc839a7ec7d992aa07e1074cdc976ddc225cedd63f36907f51cc3ce41ffc8de6` |
| Commands | `python -m moex_arbitrage_lab.research.seconds_screen confirm --config config/experiments/h0001_seconds_screen_v0_2.toml --seconds-run-id p3-20261102-v1 --analysis-run-id p3-discovery-20261102-v1 --code-commit 486c315…` (exit 0, 58 s); `python -m moex_arbitrage_lab.research_v03.cli confirm --config config/experiments/h0001_multiscale_v0_3.toml --analysis-run-id h0001-v03-20261103 --code-commit e7f6fe0… --v0-2-analysis-run-id p3-discovery-20261102-v1` (exit 0, 11 s) |

Pre-freeze verification of v0.3: independent stdlib re-implementation on September matched the
official statistics bit-for-bit (observed and null); independent review READY_TO_FREEZE after
three holdout-control fixes; guarded full gate 1204 passed, 1 skipped, coverage 92.34 %.
Generated reports remain git-ignored.
