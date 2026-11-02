# H-0001 — COMEX leads MOEX GOLD

Status: preregistration draft; no results observed.

## Hypothesis

Movements in the matching COMEX Gold Futures contract predict later same-direction changes in MOEX USD-quoted GOLD. Primary direction is COMEX → MOEX. The reverse direction is a diagnostic control.

Candidate contracts are GCZ6 and GDZ6; October 2026 is a candidate month. Both become fixed only after contract metadata, session overlap, liquidity and data coverage are verified.

## Unit of analysis and clocks

Raw trade events retain source precision. Primary clock is verified exchange event time. Vendor receive time is analyzed separately where present and must not be mixed into the event-time series. A source with undocumented event-time semantics cannot support a causal conclusion.

Simultaneous trading intervals are the intersection of actual venue sessions after breaks/halts. Analyses are split by day and meaningful session segment; no return spans a closed interval or break.

Before any response plot, each horizon receives `SUPPORTED`, `UNSUPPORTED_BY_DATA` or `TIMESTAMP_SEMANTICS_INSUFFICIENT`. Support requires observed timestamp quantum finer than the horizon, clock uncertainty not comparable to it, adequate event density, and robustness to a preregistered plausible clock-offset sweep. Nanosecond storage alone does not establish nanosecond clock accuracy. Offset bounds are fixed from documentation/QA without holdout outcomes.

## Outcomes and predictors

- Predictor: COMEX log trade-price change or signed event move over a preregistered backward window.
- Outcome: subsequent MOEX log trade-price change over horizon `h`, starting no earlier than predictor cutoff.
- Secondary: cross-correlation of sampled returns, response function conditional on COMEX move, distributed-lag regression and event study of large COMEX moves.
- Price-level correlation is prohibited as primary evidence.

Trade price is a screening proxy, not an executable quote. Aggressor side is used only after source semantics are verified.

For any cutoff `t`, venue state is the last eligible event with timestamp **strictly less than** `t`. A primary predictor window `w` uses only past COMEX changes, while the MOEX response covers future horizon `h` without crossing a session boundary. Primary evaluation points are selected by COMEX activity or a fixed causal grid, never by the existence of a future MOEX update. The primary `w` is chosen mechanically from supported timestamp/density facts before response curves are inspected; searched alternatives join the multiplicity family.

## Lag/horizon family

Mandatory: 1, 5, 10, 25, 50, 100, 250, 500 ms; 1, 2, 5, 10, 30, 60 s. Add 2 and 5 minutes as secondary horizons if session continuity and data density support them. Every unsupported cell remains visible with a reason.

Both positive and negative lags are plotted symmetrically. Primary inference family is all supported positive COMEX → MOEX lags under the frozen primary sampling rule; robustness families are labeled separately.

## Sampling and stale-trade controls

1. Event-triggered response: after an eligible COMEX move at `t`, observe MOEX change strictly after `t` at each horizon.
2. Refresh-time sampling: advance only when both markets have refreshed, preserving causal order.
3. Backward as-of grid: last observation at or before grid time, with preregistered staleness caps derived from training-day gap distributions.

Sensitivity uses at least two reasonable caps and excludes intervals where MOEX age exceeds the cap. No unlimited forward fill. Equal-time ties are aggregated or excluded unless an authoritative sequence orders them. Report age distributions and the fraction removed by every cap.

Additional asynchronous checks are lagged Hayashi–Yoshida covariance, analysis restricted to price-changing trades, and time/direction to the next MOEX price-changing update with right-censoring at breaks/session ends. A synthetic known-lag fixture must prove the estimator's sign convention.

## Estimation

Primary estimand is a horizon-specific predictive local projection `Y_M(t,h) = alpha_day,h + beta_h X_C(t,w) + Gamma_h Z(t) + error`, where `Z(t)` contains only lagged information available at `t`. Publish both an unadjusted specification and a frozen parsimonious control specification. Show cumulative and incremental responses; the latter locates reaction timing without confusing it with the cumulative maximum.

- Cross-correlation on returns with observation counts for every lag.
- Response curves with median/mean and robust confidence intervals.
- Distributed-lag prediction compared out-of-sample with a no-change and MOEX-only autoregressive baseline.
- Cluster/block-aware uncertainty: resample whole days when there are enough days; within-day moving/stationary blocks for exploratory one-day diagnostics. Block length is selected from dependence diagnostics on training data, not holdout outcomes.
- Family-wise or false-discovery control (Holm for the primary finite lag family; secondary results clearly exploratory).
- Report effect in bp per 1 bp COMEX move, simultaneous and pointwise intervals, effective event count, per-day estimates, leave-one-day-out and the strongest-day share.

One-day results are diagnostics and cannot establish stability or support a final PASS.

## Split and holdout

- One-day slice: plumbing/QA and exploratory curves only.
- Five-day pilot: freeze parser/alignment and check per-day sign/shape; no final claim.
- Full month: predeclare chronological train/validation/holdout boundaries before opening outcome reports. Suggested starting point is 60%/20%/20% by complete trading days, adjusted only to keep whole days and documented in `HOLDOUT_LEDGER.md`.
- Holdout is opened once for the frozen primary specification. Any post-holdout change creates a new hypothesis/version and new future holdout.

## Negative controls and falsification

- permute COMEX days against MOEX days with comparable session segments;
- circular/large artificial time shifts outside the plausible horizon;
- reverse direction MOEX → COMEX;
- impossible-time control in which future COMEX appears to predict an already completed MOEX interval; any effect flags leakage/clock failure;
- compare event-time and receive-time results where both timestamps exist;
- exclude stale/low-activity intervals and near-break/near-expiry windows;
- compare alternative causal sampling rules.

A placebo distribution comparable to the real statistic defeats the causal interpretation.

## Decision rubric

`EVIDENCE_OF_LEAD_LAG` requires all of: positive-direction peak, reproducible sign and approximate horizon across days, locked holdout improvement over baselines, sampling robustness, no stale-trade-only explanation, materially weaker placebos, no single-day domination, and an effect/horizon potentially large enough to justify a later execution-feasibility study. Statistical significance alone is insufficient.

Use `NO_EVIDENCE` if suitable data and a powered protocol show no robust predictive effect; `DATA_INSUFFICIENT` for inadequate coverage/density/power; `TIMESTAMP_SEMANTICS_INSUFFICIENT` when clocks cannot support ordering; `STATISTICALLY_PRESENT_LIKELY_UNTRADEABLE` when an effect exists but its timing/magnitude is implausible after future costs.

No threshold is chosen to guarantee success. The final execution claim is outside H‑0001.

## Required outputs

Contract/session map; input manifests; timestamp/interval QA; eligibility/exclusion flow; per-day and pooled lag tables; symmetric lag plot; response curves; OOS baseline comparison; block/bootstrap details; multiplicity adjustment; placebo/reverse-direction panels; robustness matrix; result category and limitations.

