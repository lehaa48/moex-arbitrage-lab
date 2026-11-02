# P2 — COMEX/Databento trade data adapter

Status: starts only after P0 acceptance and user-created Databento account/key. May run parallel with P1. No timeseries data request is authorized by this document alone.

> **2026-11-02 (ADR-0005, append-only):** for the current screen the `trades` request below is **deferred**; the only COMEX request is `GLBX.MDP3` / `ohlcv-1s` / `raw_symbol` / `GCZ6` / `[2026-09-01, 2026-11-01)` as one batch job, under the same two-step cost gate (Step B needs explicit approval of the exact estimate). See `docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md`.

## Objective

Verify GCZ6 coverage and obtain exactly the same chosen calendar/trading overlap as P1 from `GLBX.MDP3`, schema `trades`, without any cash charge.

## Ownership

Own only `src/moex_arbitrage_lab/sources/databento_history/`, its tests/fixtures, `config/sources/databento*`, private `data/raw/databento/`, the data-usage ledger and P2 evidence. Do not edit P1 or analysis modules.

## Two-step authorization

### Step A — free estimate only

Use `DATABENTO_API_KEY` from the environment. Call only free metadata/symbology/account methods needed to verify dataset/schema/raw symbol and `metadata.get_cost` for the exact inclusive-start/exclusive-end interval. Record request parameters, returned estimate, expected bytes if available, credit balance evidence and hard-zero decision. No payload download.

Return the estimate to the user/reviewer. Stop unless there is explicit approval for Step B and evidence that no cash charge can occur.

### Step B — one approved batch

Submit one batch request for `dataset=GLBX.MDP3`, `schema=trades`, `stype_in=raw_symbol`, `symbols=GCZ6`, exact UTC interval. Do not use a continuous symbol. Download once, preserve native DBN/raw output and manifest/checksum, then normalize. Do not repeat a metered stream.

## Acceptance

- Cost ledger proves estimate and actual request remained within authorized free credit with zero cash charge.
- `ts_event`, `ts_recv`, `ts_in_delta`, `sequence`, `flags` and aggressor side are mapped according to documented semantics and full ns integer representation is preserved.
- Raw checksum and row provenance reconcile; symbol/maturity/multiplier/venue are verified from definitions.
- Timestamp/sequence/gap/session/data-quality-flag QA and lag-support matrix are emitted.
- Interrupted, partial or mismatched responses fail closed and never overwrite raw.
- No key or account data appears in logs/manifests.

## Evidence

Step A and B commands/exit codes, redacted metadata output, exact cost estimate/decision, request manifest, bytes/SHA-256, counts/QA, definitions, license record and tests. Stop without downloading if any cost/license/coverage fact is ambiguous.

