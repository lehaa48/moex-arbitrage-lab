# Project status

Last updated: 2026-11-03 (Asia/Riyadh)

## Update 2026-11-03 (later): October opened — v0.2 and v0.3 both SCREEN_POSITIVE

- User authorized (2026-11-03) v0.3 registration and a single October opening. v0.3 registered
  (`e7f6fe0`, protocol `docs/experiments/H-0001_MULTISCALE_V0_3.md`), calibrated on September
  and frozen (HL-0003, `57839fe`) before October was opened.
- October results (`docs/experiments/H-0001_OCTOBER_CONFIRMATION_RESULT.md`):
  - v0.2 frozen 1 s screen: **SCREEN_POSITIVE** (r 0.216 at h*=1 s vs null threshold 0.0038;
    19/19 included days positive; controls clean).
  - v0.3 multi-scale: **SCREEN_POSITIVE**, next-bar r 0.291 / 0.220 / 0.136 / 0.049 at
    5 / 10 / 30 / 60 s, all family-wise p = 1/2001, reverse direction ≈ 0 or small negative.
  - Combined decision: **PROCEED** — the COMEX → MOEX next-bar dependence of last-trade prices
    is confirmed; whether MOEX quotes lag and whether anything is executable is not shown.
- October is now training evidence (HL-0002-OPENED, HL-0003-OPENED).
- Next candidate stage (needs explicit authorization): read-only forward GDZ6 order-book
  recording via T-Bank Invest API (user-set read-only token).

## Update 2026-11-03: audit and September-only exploration

- Independent multi-agent audit of HEAD `3fbe051`: raw data, lineage, September discovery
  (reproduced exactly by independent code), causality and holdout all PASS; one MAJOR finding —
  frozen config/code hashes are byte-level over CRLF working-tree files (LF equivalents recorded
  in HL-0002-ADDENDUM-1; do not edit or re-check-out research code/config before October).
- September-only exploration (not a protocol result): a COMEX → MOEX next-bar dependence at
  1–60 s bars (next-bar r 0.21 / 0.29 / 0.28 / 0.16 / 0.10 at 1 / 5 / 10 / 30 / 60 s; ≈0 from
  120 s; family-wise permutation p = 0.0005; reverse ≈0); the 10 day-gate exclusions do not
  change it; mechanism (real MOEX lag vs timestamp offset) unresolved.
- Crude paper economics: gross ≈0.5 bp per trade vs MOEX taker exchange+clearing fee
  1.32 bp per side; taker round trips do not cover costs; maker economics need order-book data.
- Databento: user confirmed on 2026-11-03 that no charge occurred.
- October remains **UNOPENED**. Details: `docs/experiments/H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md`.
- Next stages (each needs explicit authorization): H-0001 v0.3 registration + single October
  confirmation; read-only forward GDZ6 order-book recording.

## Current stage (historical text below; see the latest update above)

**H-0001 v0.2 is frozen after September discovery; October confirmation remains UNOPENED.**
Both months of MOEX and COMEX raw data are locally published and verified. Offline task
`docs/tasks/P3_SECONDS_PROCESSING_AND_DISCOVERY.md` built checksummed one-second series and
selected `h*=1 s` from September only. This is a discovery result, not evidence that lead-lag
exists. `confirm` was not run and no October outcome was computed.

The one-day P1 → P3 → five-day → P4 route is deferred, not deleted. Historical status text
that said data had not yet been downloaded is superseded by the verified evidence below.

## P3 seconds/discovery freeze evidence

- Code commit: `486c315ff422004bb76f97add297ef5daa66e069`.
- Research code SHA-256: `4d4acef31a32a93bef272fc6bc6c5b4d3fafb5381e527e3591077b999e1adb5e`.
- Config SHA-256: `6619460ef629a1cd0c2e5e5a8c0d0ff01f2fd8990b5c15370b651c01c3000315`.
- Seconds manifest SHA-256: `dc43c416d54e3170feb489384d3c3beb160976579669876a40b9dd06d4b35222`.
- September discovery SHA-256: `999003a3cb13b1a8192198eaeb471259286481c272025b999679a97f6afe4cb7`.
- September primary days: 21; included after frozen quality rules: 11; excluded for MOEX
  stale fraction `>= 0.5`: 10; missing primary files: 0.
- Selected `h*=1 s` by the frozen argmax/tie rule. October outcome files and
  confirmation-opened markers are absent.

## P0 acceptance evidence

- P0 was accepted on 2026-11-02 after two bounded corrective passes.
- Independent acceptance: targeted `104 passed`; guarded full gate `385 passed, 1 skipped`;
  coverage `97.76%`; ownership/recovery findings closed.
- Exact out-of-root cleanup (all user-authorized, literal-path, each property re-verified
  before deletion; neighbours unchanged):
  - `C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\{52e99d57eb652d4d,55aed9fbc3fc0dea,97d7aae16c3003c7,92d9c6e269209859}.msgpack`
    — absent;
  - `C:\Users\Aleksey\AppData\Roaming\uv\python\cpython-3.12.15-windows-x86_64-none\Lib\encodings\__pycache__\ascii.cpython-312.pyc`
    (2575 B, created/written 2026-11-02T15:52:17.2149890+03:00, Archive, one hard link)
    — removed 2026-11-02 ~16:32 +03:00, absent; its 15 neighbours and the directory unchanged.
- Guarded storage check after cleanup: PASSED.

## Gate state

| Gate | State | Exit evidence |
|---|---|---|
| A0 planning | accepted for P0 | documents, ADRs, source matrix, migration manifest |
| P0 bootstrap/migration | accepted 2026-11-02 | isolated environment, storage guard, tests and migration evidence |
| P1/P2 acquisition for v0.2 | complete | 44 MOEX weekday files; two adjacent COMEX publications; immutable raw manifests |
| Legacy P3/five-day/P4 route | deferred (ADR-0005) | retained as later research design |
| H-0001 v0.2 seconds build | complete | 88 venue output files; manifest/reconciliation/carry checks verified |
| H-0001 v0.2 September discovery | complete | `h*=1 s`; discovery artifact frozen |
| H-0001 v0.2 October confirmation | **SCREEN_POSITIVE** (opened 2026-11-03) | `confirmation.json` `135a4067…`; HL-0002-OPENED |
| H-0001 v0.3 multi-scale confirmation | **SCREEN_POSITIVE**, combined PROCEED (opened 2026-11-03) | calibration `bdd5f382…`, confirmation `bc839a7e…`; HL-0003-OPENED |
| Order-book recording | not started | separate user authorization and read-only token required |

## Stage history

| Date | Event |
|---|---|
| 2026-11-02 | User authorized P0; implementation and two corrective passes completed. |
| 2026-11-02 | Initial commit `9a28c34` pushed to `origin/main`. |
| 2026-11-02 | User pivoted to the two-month one-second screen before any outcome (ADR-0005). |
| 2026-11-02 | MOEX 44 weekday files and two adjacent COMEX publications verified offline; combined COMEX rows `1,385,912`. |
| 2026-11-02 | Foundation commit `486c315` recorded the acquisition adapters and seconds pipeline. |
| 2026-11-02 | P3 offline build/discovery completed: seconds manifest `dc43c416...`, discovery `999003a3...`, `h*=1 s`; October remains UNOPENED. |
| 2026-11-03 | Independent audit + September-only exploration recorded (`H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md`); user confirmed zero Databento charge; October still UNOPENED. |
| 2026-11-03 | v0.3 registered (`e7f6fe0`) and frozen after September calibration (`57839fe`); October opened once: v0.2 and v0.3 SCREEN_POSITIVE, combined PROCEED. |

## External and safety state

- Historical acquisition approvals and zero-cash gates are recorded in P2B/P2C.
- This offline P3 made no provider request and read no API key.
- Agents never request, print or persist tokens.
- Raw data remains immutable and generated raw/derived/report/log files remain ignored by Git.
- No trading or order-entry capability is authorized or present.
- MOEX equal-timestamp ordering uses stable physical source-row order because the archive has
  no verified sequence field; exact within-timestamp exchange order remains unresolved.
