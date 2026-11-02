# H-0001 v0.2 — two-month one-second screen (COMEX → MOEX gold)

Status: **protocol frozen before any trade payload was downloaded and before any outcome was computed** (2026-11-02). Source of the decision: user pivot `docs/tasks/CLAUDE_SECONDS_SCREEN_PIVOT_PROMPT.md`.

## Relation to H-0001 v0.1

H-0001 v0.1 (`H-0001_COMEX_LEADS_MOEX_GOLD.md`) and its strict sub-second/full research plan are **not deleted; they are deferred**. For the current screening decision, v0.2 **supersedes** the sequence one-day (P1/P3) → five-day pilot → full month (P4). A one-day canary exists only as an automatic format check inside the same acquisition run, not as a separate stage or analysis.

## Question and limits of inference

Does a reproducible one-second predictive lag COMEX → MOEX exist that is large enough to justify a deeper, more precise study? This is a trades/bar-based screen, not a test of executable trading. Allowed final categories only: `SCREEN_POSITIVE`, `NO_SCREEN_SIGNAL`, `DATA_INSUFFICIENT`, `TIMESTAMP_SEMANTICS_INSUFFICIENT`. A positive screen only permits proposing a next stage with trade-level/top-of-book data.

## Frozen universe

- Period: `[2026-09-01T00:00:00Z, 2026-11-01T00:00:00Z)`; September = discovery/training, October = locked confirmation.
- MOEX: archive identifier `GDZ6_SPBFUT` (T-Bank historical anonymized trades), canonical `MOEX:GD:2026-12-18`; primary rows `TRADE_SOURCE=EXCHANGE`; raw bytes immutable; UID status `OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED` (exactly one UID across all target rows, else fail closed).
- COMEX: `GLBX.MDP3`, schema `ohlcv-1s`, `stype_in=raw_symbol`, symbol `GCZ6`, one batch job for the whole period. Same December contract both months; no continuous symbol, no roll.
- Candidate dates: all calendar dates. MOEX requests: Mon–Fri only (weekends excluded from requests, listed as `weekend_not_requested`); canary = first GET `2026-09-02`, then remaining dates chronologically, ≤ 20 files/min (provider limit 30/min/IP).
- Primary day universe: verified common regular Mon–Fri sessions. Pre-outcome calendar exclusions (reported separately): weekends (`weekend_not_primary`), `2026-09-07` (`us_labor_day_cme_holiday_schedule`), `2026-10-12` (`us_columbus_day_cme_schedule_unverified_conservative`). Missing/incomplete files are coverage evidence and never replaced by another day.

## Calendar and timestamp facts (sources retrieved 2026-11-02)

- MOEX derivatives schedule (MSK = UTC+3), date-specific: from 2026-07-14 opening auction 06:50–07:00, morning 07:00–10:00, main 10:00–19:00, evening 19:00–23:50 (moex.com/n101980); from 2026-09-14 opening auction 06:50–07:00, morning 07:00–09:00, main 09:00–19:00, evening 19:00–23:50 (moex.com/n103379). The opening auction is never a continuous segment. Trading day = calendar day since 2026-03-23 (moex.com/n98363); weekend sessions belong to the next working day and are not primary. October 2026 **had** weekend sessions; the weekend session of 24–25 October was separately cancelled (moex.com/n96571). No weekday MOEX holiday in September/October 2026.
- T-Bank `TRADE_TS`: documented "дата и время сделки по UTC"; observed example has 6 fractional digits; clock accuracy and capture point undocumented; `DIRECTION` semantics undocumented (never aggressor side). Probe (one HEAD, 2026-10-06): HTTP 200, `application/octet-stream`, file `GDZ6_SPBFUT_2026-10-06.csv.gz` (gzip CSV, not ZIP), no auth.
- Databento `ohlcv-1s`: OHLCV only for seconds with at least one trade; `ts_event` marks the start of `[t, t+1s)`; bars are built from `ts_recv` of the source trade messages. The COMEX series therefore reflects **provider observation time**, not matching-engine event time; this limitation is carried into the result category and limitations, not corrected by invented precision.
- COMEX GC Globex: Sunday–Friday with a daily 60-minute break (16:00–17:00 CT = 21:00–22:00 UTC during CDT, which lasts through 2026-11-01). MOEX continuous windows (≤ 20:50 UTC) lie inside COMEX open hours.

## Seconds construction (per venue, separately)

Schema `second_utc,open,high,low,close,volume,trade_count,age_ms,refreshed,session_id,trading_date`; half-open seconds; a bar's close is available only after the end of its second; empty seconds are never backfilled; causal last-known state only within one continuous segment; no carry across auction, break, session or day boundaries; staleness cap primary 5 s, frozen sensitivities 1 s and 10 s; Databento `trade_count` = null (not provided). Full raw timestamp precision is retained separately. Manifests list raw SHA-256, parser/config/code identity, counts/reconciliation and output SHA-256.

## Frozen analysis

- One-second log returns; Pearson `corr(COMEX r_t, MOEX r_{t+lag})`; positive lag ⇒ COMEX leads. Lag grid (s): `-60,-30,-10,-5,-2,-1,0,+1,+2,+5,+10,+30,+60`. Price-level correlation prohibited.
- Per day/lag/cap: r, paired N, refresh/stale fractions, exclusions. Month statistic: equal-day-weighted mean of Fisher-z daily correlations, back-transformed; pooled r diagnostic only.
- September discovery: `h*` = argmax over `{1,2,5,10,30,60}` of the September equal-day statistic (cap 5 s), tie → smaller lag, fixed even if all ≤ 0. Freeze code commit + config hash + `h*` + data manifests and append the holdout ledger **before** computing any October outcome. October format/coverage/QA is not outcome but is never used to tune lags, caps, thresholds or exclusions.
- October confirmation: equal-day Fisher-z mean at frozen `h*` (cap 5 s); one-sided null from ≥ 2000 deterministic whole-day permutations (fixed seed); secondary simultaneous grid result with the 95 % max-statistic threshold over `{1,2,5,10,30,60}`. Controls: reverse direction at symmetric lag, day-mismatch permutation, large circular shift placebo, impossible-time/future-leak control, caps 1/5/10 s, per-day sign table and strongest-day share, September vs October.
- Data gate: ≥ 15 eligible regular October days; ≥ 3600 eligible paired seconds per included day at cap 5 s; stale/excluded fraction < 50 % per venue/day; published timestamp semantics/quantum and common-session map; raw → seconds → aligned reconciliation without unresolved mismatch; strongest-day contribution < 35 % of the absolute aggregate signal.
- Decision: `SCREEN_POSITIVE` only if all of: data gate passed; October r at `h*` > 0 and above the one-sided 95 % null threshold; ≥ 60 % of eligible October days positive at `h*`; reverse direction weaker in absolute value; day-mismatch, circular-shift and impossible-time controls do not reproduce the effect; sign holds for ≥ 2 of 3 caps including 5 s; no single-day domination. Data gate passed but any condition failed → `NO_SCREEN_SIGNAL`. No lag or threshold is changed after results. If the method changes after October is viewed, October becomes training evidence and is not called confirmation.

Exact numeric parameters, seeds and exclusions are frozen in `config/experiments/h0001_seconds_screen_v0_2.toml`; its SHA-256 and the code commit are recorded in `HOLDOUT_LEDGER.md` before October is opened.

## Cost gate (COMEX)

Step A: free metadata/symbology/account calls and `metadata.get_cost` for the exact request only. Stop before Step B and return exact request, estimate (USD/bytes), redacted credit facts, `FREE_CONFIRMED` or `STOP_USER_DECISION`, and proof that no identical payload is cached. Step B requires explicit user approval of that exact estimate even if it is zero (ADR-0001, cost policy). Non-zero cash charge is prohibited; no stream, no paid canary, no repeated metered request.
