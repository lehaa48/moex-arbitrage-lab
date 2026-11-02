# H-0001: independent audit and September-only exploration (2026-11-02/03)

Status: **exploratory record, not a protocol result.** Everything below uses September 2026
only (the already-opened discovery month of HL-0002). October 2026 remains **UNOPENED**: no
October return, correlation, lag statistic or price response was computed or viewed; `confirm`
was not run; no confirmation marker exists. Numbers here must not be cited as confirmation of
H-0001 and must not be used to alter the frozen v0.2 confirmation after October is opened.

Executed by Claude Code (main agent) with parallel read-only subagents, after Codex's commits
`486c315`, `8d15d8b`, `3fbe051`. No provider/network call, no API key read, no tracked file
edited by any subagent. Working scripts and full outputs live under git-ignored
`tmp/audit/**` and `tmp/explore/**` (hashes in the appendix); they are not durable project
artifacts.

## 1. Audit of the integrated state (HEAD `3fbe051`)

| Area | Verdict | Key evidence |
|---|---|---|
| Codex commits vs Claude's pre-commit work | conditional PASS | frozen config content, Databento cost gate, holdout logic not weakened; three Codex fixes real (MOEX raw hash verified against file, code identity via content hash, unknown timestamp quantum fails the day) |
| Raw data (52 files) | PASS | SHA-256/bytes match all 336 references (manifests, commit markers, sidecars, ledgers, seconds manifest); MOEX 44 files, 1,772,271 rows, one UID `91f84d07-14a1-43a6-a6b8-648b62a41994`, all `GDZ6_SPBFUT`/`EXCHANGE`; COMEX 1,385,912 bars, instrument 41271, adjacent half-open segments, no overlap/gap, no break/weekend bars |
| T-Bank acquisition | PASS | canary 2026-09-02 first; min GET spacing 3.034 s; max 19 GETs per 60 s; all HTTP 200 |
| Lineage raw → seconds | PASS | all 88 seconds outputs exist and match the manifest; inputs match raw hashes |
| September discovery | reproduced exactly | independent code (no project imports): all 819 day/lag/cap cells equal N; max diff in r 2.2e-16; same 11 included / 10 excluded days, same `h*=1 s` |
| Look-ahead / carry / lag sign | PASS | MOEX trade at exactly t+1 counts in t+1; COMEX bar close used only after its second; no carry across sessions/Globex break; k>0 = COMEX leads |
| Full guarded gate (auditor run) | PASS | 1125 passed, 6 skipped (Git Bash absent in that environment), coverage 92.34 % |
| Holdout | PASS | no `confirmation*`/`*.opened.json`; HL-0002 UNOPENED |
| Databento cost gate | evidence gaps (see §6) | two metered jobs, estimates USD 0.904719159007 + 4.154946878552; user confirmed on 2026-11-03 that **nothing was charged** |

### Findings to carry forward

- **MAJOR — line-ending dependence of frozen identities.** `seconds_config.py` and
  `seconds_store.code_sha256` hash raw file bytes. The recorded config/code hashes
  (`6619460e…`, `4d4acef3…`) are of the CRLF working-tree files on this Windows checkout;
  Git stores LF (`.gitattributes eol=lf`). LF equivalents: config
  `ce91e94cdc13fc5f3b084ca466ac393a889e89e22d76c19b769d7988752cf3ee`, research code
  `3aa8f358ef0e76ec4801edc05c840a34e4c76d71896b0bd65482a0a3cc9aa7ef`. Content is identical;
  a fresh LF checkout makes `confirm` refuse (fail-closed). Do **not** edit research code or
  config or re-checkout these files before October is opened; both hash pairs are appended to
  the holdout ledger.
- MINOR: `code_commit` is format-checked only; the real T-Bank input route in
  `seconds_screen.py` is not covered by tests; `month_statistics` silently drops days with
  undefined r (no effect in September: 11 days everywhere); `confirm` has no technical
  authorization switch (procedural only); the seconds-manifest reconciliation omits the 1,187
  opening-auction rows (traceable via `period_qa.json`); GCZ6 definition/expiry not verified.
- Process: two exploratory subagents and one reviewer each ran system Python once outside the
  guarded environment (read-only, no raw/October data); one auditor attempted to read Codex
  session logs under `C:\Users\Aleksey\.codex\sessions`, which the permission system blocked.

## 2. Why 10 September days were excluded (answer to the user's objection)

- The staleness cap is applied **per second**: a 1 s return is valid iff both endpoint prices
  are ≤ 5 s old; a fresh unchanged price yields a valid zero return (76 % of valid MOEX returns
  on included days are exactly zero), so "COMEX moved, MOEX did not trade" is kept while the
  MOEX price is fresh. The first return after a > 5 s pause is also invalid.
- The day gate (`stale fraction < 0.5` per venue over the whole common session) removed
  09-01…09-15 (MOEX stale 0.61–0.83). Cause: liquidity was in GDU6 until its expiry
  **2026-09-18** (moex.com/ru/contract.aspx?code=GDU6); GDZ6 main-session trades jump from
  6–18 k/day to 33–52 k/day from 09-16. A contract-choice consequence of the pre-registered
  "one December contract" rule, not a data defect. Main-session-only gating would not have
  rescued those days (main-session stale 0.55–0.79).
- Sensitivity (September only): the lag profile does not depend on the gate.

| Variant (cap 5 s unless noted) | days | lag 0 | lag +1 | lag +2 | lag −1 |
|---|---|---|---|---|---|
| Frozen v0.2 | 11 | 0.329 | 0.214 | 0.111 | 0.012 |
| No day gate | 21 | 0.327 | 0.210 | 0.106 | 0.003 |
| Main session only | 21 | 0.332 | 0.208 | 0.109 | 0.002 |
| No staleness cap | 21 | 0.248 | 0.157 | 0.086 | 0.009 |
| 5-min window masking | 21 | 0.350 | 0.222 | 0.116 | −0.000 |

## 3. Multi-scale lead-lag (September, 21 days, equal-day Fisher-z mean)

| Bar Δ | same bar (k=0) | next bar k=+1 (COMEX → MOEX) | days r>0 at k=+1 |
|---|---|---|---|
| 1 s | 0.33 | 0.209 | 21/21 |
| 5 s | 0.64 | 0.292 | 21/21 |
| 10 s | 0.71 | 0.283 | 21/21 |
| 30 s | 0.83 | 0.161 | 21/21 |
| 60 s | 0.89 | 0.101 | 20/21 |
| 120 s | 0.92 | 0.027 (family p 0.12) | 13/21 |
| 180–900 s | 0.94–0.975 | −0.04…+0.003 | — |

- Day-derangement permutation null (R=2000, seed 20260901) with standardized max-T over 30
  (Δ, k>0) cells: family-wise p = 0.0005 (floor) for Δ = 1–60 s.
- Reverse direction (MOEX → COMEX) ≤ ~0.012 at short scales; tiny but sometimes nominally
  significant; negligible in size.
- Catch-up: for 1 s bars MOEX reflects 0.34 of a COMEX move in the same second and 0.68 by the
  third; for 5–10 s bars ≈0.70 same bar and ≈0.95–1.0 one bar later; ≥120 s: no lagged part.
- Robust to method: 5 s k=+1 Spearman 0.346, 0.5 % winsorised 0.326, excluding 12:25–12:45 and
  13:55–14:15 UTC 0.295.
- Lagged MOEX part after top-quintile COMEX moves ≈ 0.55–0.66 bp at 5–60 s (≈2.4–2.9 MOEX
  ticks; tick 0.1 USD ≈ 0.227 bp).
- **Mechanism unresolved.** In a fresh-price subsample (both venues ≤1 s old) the next-bar
  correlation stays 0.17 (5 s) / 0.24 (10 s), so pure MOEX trade sparsity is **not** supported
  as the full explanation; a real multi-second MOEX lag in trade prints or a multi-second
  clock offset remain open. Clock-offset diagnostic: shifting MOEX timestamps −500 ms makes the
  1 s profile symmetric (r(−1)=0.1395, r(+1)=0.1389); it cannot explain the 5–60 s effects.
- Conditioning check at 1 s: lag-1 r is 0.250 when MOEX traded in second t and 0.335 when it
  traded in t and t+1 (vs 0.214 overall) — the lead is not created by stale MOEX prices.

## 4. Price gap g = ln MOEX − ln COMEX (September)

- MOEX closes 0.8–1.0 of deviations; COMEX ≈ 0. ~65–78 % of large deviations start with a
  COMEX move. Fast component half-life ≈7–20 s; for deviations > 5 bp ≈27 % closed in 10 s,
  35 % in 60 s, 43 % in 300 s (window-dependent).
- Intraday sd of g ≈ 3–7 bp; median |deviation| ≈1.5 bp (6–7 ticks).
- **Level shift (verified from raw minute medians):** g ≈ −2 bp on 09-01…09-03, rising
  smoothly to +63…+83 bp (≈28–36 USD/oz, MOEX above COMEX) from 09-11. Not a units/contract
  mix-up (one UID, one COMEX instrument, continuity across the COMEX file boundary). Economic
  cause unexplained (sign opposite to the expiry carry ≈ −13 bp); needs GDZ6 settlement
  specification, CBR USD/RUB and RUB spot-gold data.

## 5. Crude per-trade economics (paper calculation on last-trade prices)

- 108 pre-fixed cells (signal window 5/10/30 s × threshold q90/q99 × "MOEX behind" filter ×
  entry delay 0/0.25/1 s × holding 10/30/60 s), entry and exit at the next actual MOEX trade.
  No best cell selected.
- Gross per trade, one leg: 0.22–0.81 bp, median 0.50 bp (≈0.22 USD per 1-oz contract);
  hit rate 53–59 %; entry delay 0 / 0.25 / 1 s → median 0.60 / 0.52 / 0.41 bp; q90 ≈260–650
  trades/day. Day-mismatch placebo ≈0 (median −0.01 bp); reverse placebo ≈ −0.09 bp.
- Break-even round-trip cost ≈0.4–0.6 bp; at 1 bp all 108 cells negative.
- Public fees (retrieved 2026-11-02 ~20:30–21:01 UTC; CBR 80.9734 USD/RUB for 2026-11-03;
  1 GDZ6 ≈ 356 k RUB, 1 bp ≈ 35.6 RUB):
  - MOEX exchange + NCC clearing for gold futures, **taker 0.01320 % ≈ 47 RUB per side
    (1.32 bp)**, **maker 0**; no intraday discount (MOEX tariffs eff. 2026-09-08, NCC tariffs
    eff. 2026-09-21).
  - Fixed-RUB brokers (user's broker 1 RUB/contract; ALOR, VTB) charge the exchange fee on
    top; T-Bank percentage tariffs include it (Трейдер 0.04 % per side ≈ 8 bp RT).
  - Round trip with taker fills ≥ ≈2.64 bp + spread at any broker → the September gross edge
    (≈0.5 bp) does not cover costs. Maker fills at a fixed-RUB broker cost ≈0.06 bp RT but
    their economics cannot be evaluated from trade prints.
- **Decisive open question:** whether MOEX quotes (bid/ask) move with COMEX before trades
  print. Requires order-book data; trades-only data say nothing about executability. A COMEX
  hedge leg adds ≈0.25 bp (MGC) and faces contract-size and access constraints.

## 6. Databento acquisition record (audit)

- Two metered batch jobs (adjacent, non-overlapping; sums equal the single two-month quote
  USD 5.059666037560 and 77,611,072 billable bytes). 42 HTTP events; two `batch.submit_job`.
- Approval files were written by the operator (Codex) seconds after each Step A estimate; no
  separate user-side record is referenced. `actual_cost_usd` is null for both jobs.
- **User statement 2026-11-03: the Databento Billing page shows no charge ("ничего не
  списалось").** This closes the pending zero-cash confirmation in P2B/P2C.
- Some Step A figures quoted in P2B (e.g. USD 1.024559810758 for end 2026-09-12) are not in
  `logs/databento/requests.jsonl`.

## 7. Decisions and next stages (user, 2026-11-03)

Record these results now; next stages, each requiring explicit authorization:

1. Register H-0001 v0.3 (multi-scale COMEX → MOEX at Δ ∈ {5 s, 10 s, 30 s, 60 s}, k=+1,
   reverse direction as control, day-derangement null with family-wise correction, thresholds
   fixed before opening) alongside the unchanged v0.2 test; append HL-0003 to the holdout
   ledger; then open October **once**.
2. Forward recording of the GDZ6 order book via the T-Bank API (read-only market data; token set
   locally by the user, never handled by agents) to test whether quotes lead trade prints.

## Appendix: evidence files (git-ignored, SHA-256)

| File | SHA-256 |
|---|---|
| `tmp/audit/replication/replication_result.json` | `95dfbdb68391d43f5ea158fc0e2f43670e59869ebc2f323bf3fe61ca3f8e66de` |
| `tmp/audit/replication/diagnostics_result.json` | `02af8c751bd7559e614cd50679195c9698e2482a2fca5296335b1969f98a1eae` |
| `tmp/audit/replication/diag_clock_offset_result.json` | `59b4867966b9434f95e8613ab607bca6c8da9f4cb192155916f565c4ed789b44` |
| `tmp/audit/sensitivity/results.json` | `962327545c433048c15ae8e565c88c0c63988ba7c210edec6a888ce7c93f3bc2` |
| `tmp/audit/methodology/sep_session_staleness.json` | `a92147cba1d7f9d7bdc527a877c40023b4e9480595b289fcf11e6914a5660b30` |
| `tmp/audit/data/c_raw_audit.json` | `7069d782933d5e20c95a6552fcc0385fdd6e1873f172a1368da55f638220b1c3` |
| `tmp/audit/git/full_gate.txt` | `5d6da65c3315a78e6554585127270edde4f79fcc5d35d39e2f562cccaf9bcf48` |
| `tmp/explore/multiscale/results.json` | `9bac6461b4bcf9d56d388c12e83cae6946f6ea8aa9241f79965700bd3d481f59` |
| `tmp/explore/ecm/results.json` | `68fcb1e9d659182f8dc760dd383859c552b1a790365038c06fbf6a31574cb11e` |
| `tmp/explore/econ/results.json` | `78a17c99fb2510c2a4a45256e2b20004113f301aa37a438a990ae171220385de` |
| `tmp/explore/fees/fees.md` | `d2b153a298a9fa4926b3a0c6dcc9ca2ddc21cce97063582636594c507584779d` |
| `tmp/audit/explore-review/repro_result.json` | `4ac6db4af2d3ce910634bad145789cd6278de10ce23325f6a3850fd8cfead43a` |
| `tmp/audit/explore-review/gapcheck_result.json` | `fee3324ba5835668c788541189f6fc9da3b151cf5db7bb4c6f30fe2eb59f0e2f` |
| `tmp/audit/explore-review/robust_result.json` | `660cb6d680d64bc6b3550ce5cf67d4dc196f1388857e752f81a19a0a7f2786df` |
