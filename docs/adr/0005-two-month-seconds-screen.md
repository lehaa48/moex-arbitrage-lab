# ADR-0005: Two-month one-second screen before the full H-0001 study

Status: accepted — 2026-11-02 (user pivot, before any trade payload or outcome)

## Decision

Replace, for the current screening decision, the staged one-day → five-day → full-month route with one end-to-end screen on `[2026-09-01, 2026-11-01)` UTC at one-second resolution: MOEX `GDZ6_SPBFUT` from T-Bank anonymized trades and COMEX `GCZ6` from Databento `GLBX.MDP3` `ohlcv-1s` (one batch). September is discovery, October a locked confirmation. Protocol: `docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md`.

## Consequences

- H-0001 v0.1, its sub-second plan and tasks P1–P4 are deferred, not deleted; P2 changes from `trades` to `ohlcv-1s` for this screen only.
- The COMEX series is provider-observation (`ts_recv`-based) seconds, so the screen cannot establish matching-engine lead/lag; allowed categories are `SCREEN_POSITIVE`, `NO_SCREEN_SIGNAL`, `DATA_INSUFFICIENT`, `TIMESTAMP_SEMANTICS_INSUFFICIENT`.
- ADR-0001 and the cost policy still apply: Databento Step B needs explicit approval of the exact estimate; no cash charge.
- ADR-0002/0003 still apply: immutable raw, separate clocks, no backfill, no carry across breaks.
