# Holdout ledger

The ledger is append-only. Dates are UTC unless venue trading dates are explicitly named.

| Entry | Hypothesis/version | Candidate universe | Training | Validation | Holdout | Frozen identities | Opened at/by | Reason/change |
|---|---|---|---|---|---|---|---|---|
| HL-0001 | H-0001 v0.1 | candidate October 2026, GDZ6/GCZ6 | not assigned | not assigned | **UNOPENED** | — | — | Boundaries wait for verified complete-day availability; outcomes must not be inspected first. |
| HL-0002 | H-0001 v0.2 seconds screen | `GDZ6_SPBFUT` (MOEX) / `GCZ6` `ohlcv-1s` (COMEX), regular weekday sessions | 2026-09-01 … 2026-09-30 (discovery) | — (none; h* chosen on discovery) | 2026-10-01 … 2026-10-31 (locked confirmation) **UNOPENED** | pending at registration | — | Registered 2026-11-02 before any trade payload or outcome (ADR-0005). HL-0001 v0.1 is deferred, not opened. |
| HL-0002-FREEZE | H-0001 v0.2 seconds screen | Same fixed contracts and regular weekday session intersection as HL-0002 | September discovery complete; 21 primary days, 11 included, 10 excluded by the frozen MOEX stale-fraction rule | — | 2026-10-01 … 2026-10-31 (locked confirmation) **UNOPENED** | config `6619460ef629a1cd0c2e5e5a8c0d0ff01f2fd8990b5c15370b651c01c3000315`; code commit `486c315ff422004bb76f97add297ef5daa66e069`; code SHA `4d4acef31a32a93bef272fc6bc6c5b4d3fafb5381e527e3591077b999e1adb5e`; seconds manifest `dc43c416d54e3170feb489384d3c3beb160976579669876a40b9dd06d4b35222`; discovery `999003a3cb13b1a8192198eaeb471259286481c272025b999679a97f6afe4cb7`; `h*=1 s` | — | Frozen 2026-11-02 after September-only discovery. Inclusion: Mon–Fri common regular sessions, excluding 2026-09-07 and 2026-10-12 plus weekends; quantum present and `<=1 s`, primary-cap paired seconds `>=3600`, each venue stale/excluded fraction `<0.5`, reconciliation/session map valid. `h*` is argmax over `{1,2,5,10,30,60}` at cap 5 s, tie to smaller lag. No confirmation marker exists; `confirm` requires separate user authorization. |
| HL-0002-ADDENDUM-1 | H-0001 v0.2 seconds screen | unchanged | September additionally explored (exploratory, September only): independent replication, sensitivity, multi-scale 1 s–15 min, price-gap and crude cost analyses — `H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md` | — | 2026-10-01 … 2026-10-31 **UNOPENED** | unchanged; line-ending equivalents of the frozen identities: config CRLF `6619460ef629a1cd0c2e5e5a8c0d0ff01f2fd8990b5c15370b651c01c3000315` = LF `ce91e94cdc13fc5f3b084ca466ac393a889e89e22d76c19b769d7988752cf3ee`; research code CRLF `4d4acef31a32a93bef272fc6bc6c5b4d3fafb5381e527e3591077b999e1adb5e` = LF `3aa8f358ef0e76ec4801edc05c840a34e4c76d71896b0bd65482a0a3cc9aa7ef` | — | Appended 2026-11-03 by Claude Code. No October return/correlation/lag/price response computed or viewed; `confirm` not run. Research code and config must not be edited or re-checked-out before October is opened (hashes are byte-level). Any multi-scale test (planned v0.3) must be registered as a new entry before opening October; v0.2 confirmation rules stay unchanged. |

Opening a holdout requires the frozen identities above and explicit user authorization. If the
holdout influences a redesign, that period becomes training evidence and a new future holdout
must be registered.

Frozen MOEX tie policy for HL-0002-FREEZE: observations are stably sorted by `TRADE_TS`;
equal timestamps retain physical source-row order. The archive supplies no independently
verified exchange sequence, so exact within-timestamp ordering is unresolved and remains a
scientific limitation. This policy was applied before September discovery and must not be
changed for the locked October confirmation.
