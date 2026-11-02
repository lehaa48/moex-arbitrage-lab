# P1 — MOEX/T‑Bank trade data adapter

Status: starts only after P0 acceptance. May run parallel with P2.

## Objective

Verify and acquire at zero charge one complete GDZ6 trading day from the T‑Bank historical anonymized-trades archive, preserving the raw payload and producing source QA. This task does not run lead-lag analysis or a live recorder.

## Ownership

Own only `src/moex_arbitrage_lab/sources/tbank_history/`, its tests/fixtures, provider-specific configs under `config/sources/`, generated private `data/raw/tbank/` and P1 evidence under `artifacts/`. Do not change canonical schema without an interface proposal reviewed by P0 owner.

## Gates and sequence

1. Using public documentation/metadata, resolve exact `TICKER_CC` and `INSTRUMENT_UID` for GDZ6 and verify contract metadata against MOEX.
2. Choose a liquid weekday in October 2026 by a rule independent of lead-lag outcome: complete archive, ordinary session, non-holiday, not near expiry; record candidates/rejections.
3. Run a bounded existence/status probe respecting 30 files/min/IP. Do not crawl the month.
4. Confirm response license/retention policy and expected zero cost. If auth is unexpectedly required, stop; the user supplies a read-only token later via `TBANK_INVEST_TOKEN`.
5. Acquire exactly one approved daily archive, create-once, with SHA-256 and request manifest. Filter primary analytical view to `TRADE_SOURCE=EXCHANGE` but retain raw bytes unchanged.
6. Parse deterministically into canonical `trade_v1`; never infer aggressor side from `DIRECTION` until semantics are proven.
7. Emit timestamp, session, duplicate/gap/correction, source-composition and coverage QA.

## Acceptance

- Zero recorded charge; no token printed or persisted.
- Raw checksum/retrieval/license manifest and row-level provenance exist.
- Counts reconcile from archive → CSV → parsed → eligible exchange rows, with every exclusion reason counted.
- Timestamp quantum, ties, gaps, monotonicity and first/last event by session are measured, not assumed.
- Missing/corrupt/HTML/partial archives fail closed; retry honors rate limits and does not overwrite.
- Actual support for each lag bucket is machine-readable.

## Evidence

Exact URL pattern with identifiers redacted only where sensitive; HTTP status/headers; bytes/SHA-256; counts and QA reports; source terms link; test/command logs; candidate-day decision; unknowns. Do not publish raw rows in the Git diff.

