---
name: research-review
description: Review MOEX Arbitrage Lab changes or evidence for scientific validity, temporal leakage, storage/cost safety, and task acceptance.
---

# MOEX Arbitrage Lab review

Use this skill when asked to review an implementation, experiment run, stage gate or Claude evidence bundle in this repository.

## Procedure

1. Read `AGENTS.md`, the active task, relevant ADRs, `docs/TECHNICAL_SPECIFICATION_V0_1_RU.md`, H‑0001 and the holdout ledger completely.
2. Establish the exact Git diff, task-owned file boundary, code/config/input hashes and whether the legacy pinned commit was used correctly.
3. Inspect executable code and real artifacts. Do not accept prose-only completion claims.
4. Run only the smallest relevant offline tests first. A full integration run is required after actual P0 migration and before a research release, not for documentation-only edits.
5. Audit in this order:
   - no trading capability, paid request, secret exposure or storage escape;
   - immutable raw, checksum/provenance and row-count reconciliation;
   - documented event/receive/ingest clock semantics and supported resolution;
   - backward-only/half-open joins, session breaks, tie policy and lag-sign synthetic proof;
   - stale-trade, asynchronous sampling and observation-count disclosure;
   - preregistration, holdout access, multiplicity, dependence-aware inference and placebos;
   - claims no stronger than trades-only evidence permits.
6. Report findings first, ordered critical/high/medium/low, with file:line or artifact/command evidence. State missing evidence explicitly.
7. If no findings remain, state what was checked and residual limitations; never convert absence of a bug into evidence that H‑0001 is true.

## Stop rules

Stop and return `BLOCKED` if a test would contact a provider, use a real token, submit a metered request, modify raw/holdout state or write outside the project. Do not repair implementation unless separately authorized.

