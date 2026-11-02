# P3 — offline seconds processing and September discovery

Status: **completed offline on 2026-11-02**. October confirmation remains forbidden and
`UNOPENED`.

## Objective

Verify the already-published MOEX/T-Bank and COMEX/Databento raw inputs, build causal
one-second venue series, publish complete QA/reconciliation evidence, and execute only the
September 2026 discovery step of H-0001 v0.2. Select and freeze `h*` without reading or
computing any October price-response, return, correlation, lag, or other outcome.

## Owned files

- `docs/tasks/P3_SECONDS_PROCESSING_AND_DISCOVERY.md`
- `src/moex_arbitrage_lab/research/seconds_config.py`
- `src/moex_arbitrage_lab/research/seconds_series.py`
- `src/moex_arbitrage_lab/research/seconds_analysis.py`
- `src/moex_arbitrage_lab/research/seconds_store.py`
- `src/moex_arbitrage_lab/research/seconds_screen.py`
- `tests/unit/research/`
- `tests/unit/test_repository_hygiene.py` (generated-report ignore integration)
- `src/moex_arbitrage_lab/sources/databento_history/client.py` (quality-gate-only unused import)
- `docs/PROJECT_STATUS.md`
- `docs/experiments/HOLDOUT_LEDGER.md`

Generated raw/derived data, manifests, QA, reports and logs remain ignored and are not part
of the Git-owned file set. Existing user changes outside this list are read-only context.

## Inputs

- MOEX: `data/raw/tbank/history-trades/gdz6/<YYYY-MM-DD>/gdz6_spbfut_<YYYY-MM-DD>.csv.gz`
- COMEX: the two adjacent CSV publications under
  `data/raw/databento/glbx-mdp3/gcz6/2026-09-01--2026-09-11/` and
  `data/raw/databento/glbx-mdp3/gcz6/2026-09-11--2026-11-01/`
- Existing acquisition manifests and SHA-256 evidence only; no provider/API access.
- Frozen protocol and config:
  `docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md` and
  `config/experiments/h0001_seconds_screen_v0_2.toml`.

## Stages

1. Audit immutable raw files, manifests, hashes, identities, row/time/date coverage, and the
   non-overlap of the COMEX segments. October is limited to format/coverage/quality QA.
2. Run the minimal offline baseline tests and repair only defects within the owned files.
3. Build create-once per-venue seconds series with half-open seconds, explicit clock
   semantics, causal within-segment carry and caps 1/5/10 seconds.
4. Publish per-venue/day QA and raw-to-seconds-to-aligned reconciliation, including timestamp
   ties/quantum, gaps, stale/refreshed fractions, session coverage and exclusions.
5. Run September-only `discover`; mechanically select `h*` from `{1,2,5,10,30,60}` at cap
   5 seconds, tie-breaking to the smaller lag.
6. Freeze code/config/data/discovery identities and update the holdout ledger while leaving
   October `UNOPENED`.

## Stop rules

- Do not contact Databento, T-Bank, MOEX or any other external API; do not read API keys.
- Do not modify, unpack in place, or overwrite raw files.
- Do not write project-created files outside `D:\project\moex-arbitrage-lab`.
- Do not run `confirm`; do not create a confirmation-opened marker.
- Do not compute, print or persist October outcomes of any kind. October access is restricted
  to format, coverage, identity, timestamp/session and reconciliation QA.
- Stop on raw hash mismatch, multiple MOEX UIDs, unexpected COMEX identity/schema, unresolved
  reconciliation, future interpolation/backfill, session-boundary carry, or holdout leakage.

## Acceptance criteria

- Required offline tests and the offline quality gate pass without weakening safety checks.
- Raw hashes and row/date/time/identity coverage reconcile to existing manifests; the COMEX
  segments are strictly ordered and non-overlapping.
- Seconds outputs use the frozen schema and are create-once, checksummed, reproducible, and
  linked to raw SHA-256, config SHA-256 and code identity.
- QA reports all protocol-required per-day counts, timestamp/gap/staleness/session/exclusion
  fields and proves no carry across session/day boundaries.
- Discovery reads only September outcome data, records per-day and monthly statistics, and
  selects `h*` exactly by the frozen rule.
- `HOLDOUT_LEDGER.md` records code/config/seconds/discovery identities, `h*`, and exact
  inclusion/exclusion rules; HL-0002 remains `UNOPENED`.
- A repeat build/discovery is deterministic/idempotent or safely refuses without overwrite.
- `git diff --check`, secrets/hygiene tests, and generated-artifact ignore checks pass.

## Completion evidence

- Code commit: `486c315ff422004bb76f97add297ef5daa66e069`.
- Config SHA-256: `6619460ef629a1cd0c2e5e5a8c0d0ff01f2fd8990b5c15370b651c01c3000315`.
- Seconds manifest SHA-256: `dc43c416d54e3170feb489384d3c3beb160976579669876a40b9dd06d4b35222`.
- September discovery SHA-256: `999003a3cb13b1a8192198eaeb471259286481c272025b999679a97f6afe4cb7`;
  selected `h*=1 s`.
- Full guarded offline gate: `1130 passed, 1 skipped`, coverage `92.34%`.
- `confirm` was not run; no confirmation artifact or opened marker exists.
