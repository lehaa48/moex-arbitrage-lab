# Техническое задание v0.1

## 1. Назначение

Построить минимальный воспроизводимый pipeline `raw → normalized → aligned → features → report` для проверки H‑0001 без торговых операций. Источники и научный результат должны оставаться проверяемыми независимо от кода анализа.

## 2. Компоненты

```text
Source adapters (P1, P2)
  -> immutable raw payload + request/license manifest + SHA-256
  -> venue-specific parser
  -> canonical trade schema v1 (Parquet)
  -> data QA and real-session overlap
  -> causal alignment/sampling variants
  -> preregistered H-0001 statistics
  -> immutable run evidence bundle
```

P1 и P2 не зависят друг от друга и владеют разными adapters. Общая schema и manifest contract фиксируются в P0; интеграция начинается только в P3.

## 3. Canonical trade schema v1

| Field | Type / nullability | Rule |
|---|---|---|
| `source` | string, required | vendor/source name |
| `venue` | string, required | `MOEX` or `COMEX` |
| `dataset` | string, required | provider dataset/archive |
| `instrument_id` | string, required | stable provider identifier |
| `raw_symbol` | string, required | symbol exactly as supplied |
| `canonical_contract` | string, required | explicit venue + root + expiry; never continuous |
| `asset` | string, required | `GOLD` |
| `expiry` | date, required | verified contract expiry |
| `currency` | string, required | quote currency |
| `price_unit` | string, required | e.g. USD/troy ounce |
| `contract_multiplier` | decimal/string, required | exact contract economics; do not infer from prices |
| `event_ts_utc_ns` | int64, nullable | exchange/source event time only |
| `event_ts_type` | string, required | authoritative semantics or `UNKNOWN` |
| `receive_ts_utc_ns` | int64, nullable | vendor receive time only |
| `capture_ts_utc_ns` | int64, nullable | separate capture clock if source distinguishes it |
| `local_ingest_ts_utc_ns` | int64, required | local retrieval/parse observation; never substitutes event time |
| `price` | decimal, required | preserve source precision |
| `price_ticks` | int64, nullable | exact integer ticks when tick size is verified |
| `size` | decimal, required | source units documented |
| `aggressor_side` | enum, nullable | `BUY`, `SELL`, `UNKNOWN`; source semantics recorded |
| `sequence` | int64/string, nullable | source sequence if supplied |
| `action` | enum, required | `ADD`, `CANCEL`, `CORRECT`, `UNKNOWN` |
| `flags` | list/bitfield, nullable | source data-quality/correction flags preserved |
| `trade_source` | string, nullable | retain raw classification; P1 defaults to `EXCHANGE` |
| `session_id` | string, nullable | assigned only from verified calendar rules |
| `trading_date` | date, required | venue calendar, not naïve UTC date |
| `source_file` | string, required | relative immutable raw path |
| `source_record_id` | string, required | deterministic within raw payload |
| `source_row_no` | int64, required | physical record/row location for traceability |
| `schema_version` | string, required | `trade_v1` |
| `raw_payload_hash` | fixed 64-char hex, required | SHA-256 of entire raw payload |

Precision metadata belongs in the dataset manifest: source unit, observed timestamp quantum, unique/tied counts and timestamp semantics. Parquet timestamp conversion must not truncate the int64 nanoseconds.

## 4. Raw and normalized contracts

Every raw acquisition publishes payload and manifest atomically. A manifest includes provider, exact parameters, retrieval UTC, estimated/actual cost facts, license URL/version, HTTP/API result metadata without secrets, bytes and SHA-256. Existing payloads are never overwritten.

Normalization is deterministic for `(raw SHA-256, parser version, schema version, config hash)`. It emits row count, exclusions by reason, min/max times, duplicates, corrections, timestamp QA and output checksum. Invalid records are quarantined with non-secret reason codes; they are not silently dropped.

## 5. QA gates

For each venue/day report:

- actual timestamp resolution and semantics;
- rows, unique event timestamps, tied-event share, null timestamps;
- inter-trade gap quantiles and gaps by session;
- duplicates by stable identity and by payload fields;
- out-of-order/sequence gaps, corrections/cancellations;
- price/size domain checks and `TRADE_SOURCE` composition;
- first/last event against the verified venue calendar;
- sessions present, halts/breaks and unmatched intervals;
- event-vs-receive latency distribution when both are valid.

Sub-millisecond tests are reported `UNSUPPORTED_BY_DATA` if the source clock/data density cannot resolve them; precision is never fabricated.

## 6. Causal alignment

- Sort deterministically by event time plus source sequence/tie policy.
- Restrict to verified simultaneous open intervals before computing returns.
- Any as-of join uses only values with `source_ts <= target_decision_ts` and an explicit staleness cap.
- Refresh-time sampling and event-triggered response are primary robustness alternatives.
- No future interpolation, centered window or unconstrained forward fill.
- Equal timestamps are not assumed to reveal a causal order unless sequence semantics support it; sensitivity analysis excludes/aggregates ambiguous ties.
- Predictor state at cutoff `t` uses only events strictly before `t`; response windows are half-open and synthetic boundary tests fix the lag-sign convention.

## 7. Experiment and inference

The frozen protocol is in `experiments/H-0001_COMEX_LEADS_MOEX_GOLD.md`. Required lag grid: 1, 5, 10, 25, 50, 100, 250, 500 ms; 1, 2, 5, 10, 30, 60 s; plus preregistered multi-minute horizons supported by data. Analysis uses returns/price changes and reports both lag signs, while COMEX → MOEX is the sole primary direction.

Inference must be time-dependence-aware (day/session clusters plus moving/stationary or wild-cluster bootstrap), correct the lag/method family, report simultaneous confidence intervals/effect sizes and retain per-day and leave-one-day-out results. Lagged Hayashi–Yoshida covariance and next-MOEX-update survival/direction analysis are asynchronous robustness estimators, not substitutes for the primary predictive response. Placebo and reverse-direction diagnostics must not be reframed as new primary hypotheses.

## 8. Evidence bundle

Each run contains config and Git commit, environment/lock identity, input/output checksums, row/exclusion counts, session map, QA tables, figures/tables, machine-readable metrics, logs, cost ledger reference, holdout status and a signed-off result category:

- `EVIDENCE_OF_LEAD_LAG`;
- `NO_EVIDENCE`;
- `DATA_INSUFFICIENT`;
- `TIMESTAMP_SEMANTICS_INSUFFICIENT`;
- `STATISTICALLY_PRESENT_LIKELY_UNTRADEABLE`.

No report may call a trades-only estimate executable arbitrage or profit.

## 9. Non-functional requirements

- Python 3.12 target, reproducible lock created in P0, strict typed core where practical.
- Fail closed on storage/root, paid-data, trading-mode, secrets and schema mismatches.
- Unit tests use synthetic fixtures only; network tests are explicitly marked and never run by the default quality gate.
- One implementation agent owns a file at a time; review evidence is independent of prose claims.

