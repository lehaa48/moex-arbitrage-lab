---
name: p1-moex-zero-cost
description: Rules and stop gates for P1 — zero-charge acquisition of one GDZ6 trading day from the T-Bank historical anonymized-trades archive, immutable raw publication, trade_v1 parsing and source QA. Use whenever working on P1 or the tbank_history adapter.
---

# P1 — MOEX/T-Bank one-day ingest at zero cost

Read completely first: `AGENTS.md`, `CLAUDE.md`, `docs/tasks/P1_MOEX_TRADE_DATA.md`, ADR-0001…0004, `docs/TECHNICAL_SPECIFICATION_V0_1_RU.md`, `docs/STORAGE_AND_SECRETS_POLICY.md`, `docs/COST_AND_PURCHASE_POLICY.md`, H-0001 and the holdout ledger (never open holdout outcomes). Load `parallel-stage-execution` for agent roles.

## Ownership (from the P1 task)

Only `src/moex_arbitrage_lab/sources/tbank_history/`, its tests/fixtures, provider configs under `config/sources/`, private raw under `data/raw/tbank/` and P1 evidence under `artifacts/`. Common contracts, `pyproject.toml`/`uv.lock`, P2/P3/P4, H-0001 and the holdout ledger change only through an interface proposal approved by the reviewer/user.

## Stop gates before any payload request (main agent collects and cross-checks)

1. GDZ6 identity from official sources: `TICKER_CC`, `INSTRUMENT_UID`, venue/root/expiry, contract economics; agree with MOEX.
2. Archive URL, schema, field semantics, rate limit (30 files/min/IP), license/retention terms and zero-charge evidence from official T-Bank documentation.
3. October 2026 calendar; a small candidate-day list; one complete ordinary weekday chosen by a mechanical rule written down **before** looking at any data and independent of any lead-lag outcome (non-holiday, ordinary session, complete archive, not near expiry). Record candidates and rejections.
4. A bounded probe plan: exact request, expected status, content type and size range, and stop conditions.
5. Confirmation that no token is required. If authentication is required: stop; the user sets `TBANK_INVEST_TOKEN` locally later. Never request, print or persist a token.
6. `recover_raw_publications()` preflight with `report.clean` before any publication.

Any ambiguity in identity, calendar, license, cost or expected response → return `BLOCKED`, download nothing.

## Network (main agent only, sequential)

Exactly one bounded existence/status probe, then exactly one approved daily archive download. No month scans, no parallel calls, no retries that could exceed the rate limit. Stop on auth challenge, possible charge, HTML/error page, unexpected content type/size, partial or corrupt archive. Record status and non-secret headers, bytes, SHA-256 and UTC retrieval times.

## Raw and parsing rules

- Raw bytes are published unchanged with the P0 protocol (`publish_raw`: manifest, checksum, commit marker; then `verify_published`). Retries or races never overwrite raw.
- Deterministic parsing to `trade_v1` with `source_file`, `raw_payload_hash`, `source_record_id`, `source_row_no` provenance; missing source times stay null; `local_ingest_ts_utc_ns` never substitutes event time.
- Keep every row in raw; the primary eligible view is confirmed `TRADE_SOURCE=EXCHANGE`. Do not treat `DIRECTION` as aggressor side until official semantics are proven.
- Reconcile archive → CSV → parsed → eligible, counting every exclusion reason.

## QA (measure, never assume)

Timestamp semantics and quantum, nulls, ties, ordering/monotonicity, inter-trade gaps, duplicates, corrections/cancellations, sessions with first/last event, source composition, and machine-readable support for every preregistered lag bucket. No lead-lag analysis and no conclusion about H-0001.

## Tests and Git

Synthetic targeted tests first, then bounded mocked failure/integration tests, then one guarded full gate after integration. Raw/generated data and credentials stay git-ignored. Status after execution: `executed; awaiting Codex/user review` — never self-accept.
