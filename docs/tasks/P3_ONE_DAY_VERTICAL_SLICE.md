# P3 — One-day vertical slice

Status: blocked by accepted P1 and P2 evidence for one common day.

> **2026-11-02 (ADR-0005, append-only):** **deferred.** No separate one-day slice is run; the two-month seconds screen (`docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md`) replaces it for the current decision.

## Objective

Integrate immutable MOEX and COMEX inputs into a reproducible one-day diagnostic run. This validates plumbing and temporal correctness; it cannot confirm H‑0001.

## Ownership

Own `src/moex_arbitrage_lab/{normalization,alignment,research}/`, integration tests, run config and generated P3 evidence. Source adapters remain owned by P1/P2; propose interface changes instead of editing them concurrently.

## Work and outputs

1. Lock input manifests, contract map and date-specific session/status map.
2. Produce deterministic canonical Parquet and reconciliation reports.
3. Calculate actual simultaneous continuous intervals; exclude boundaries/breaks/gaps and apply warm-up.
4. Run timestamp eligibility before price response. Mark unsupported horizons explicitly.
5. Implement strict backward as-of/half-open boundaries, staleness age, refresh-time and event-response views.
6. Produce symmetric-lag cross-correlation, COMEX→MOEX response/local-projection table and reverse/placebo diagnostics on returns.
7. Run synthetic known-lag, reverse-lag, equal-timestamp, future-leak, break-boundary, coarse-clock and reproducibility tests.

Required artifacts: normalized manifests/checksums, `timestamp_qa`, `overlap_report`, `data_quality_report`, run manifest/config hash, lag/response table, observation counts, lead-lag/staleness plots and `H-0001` one-day report labeled exploratory.

## Acceptance

Every normalized row traces to immutable raw; repeated run is identical; no future row enters a predictor; no window crosses a break; lag sign is proven by synthetic data; unsupported precision is not analyzed; counts reconcile at every filter; report makes no execution/profit claim.

