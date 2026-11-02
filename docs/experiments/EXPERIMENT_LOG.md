# Experiment log

Append one row before execution and complete it without rewriting history.

| Run ID | Experiment/version | UTC created | Code commit | Config hash | Input manifest IDs | Split/holdout state | Purpose | Result category | Evidence path |
|---|---|---|---|---|---|---|---|---|---|
| — | H-0001 v0.1 | 2026-11-02 | planning only | — | — | unopened | preregistration | — | `H-0001_COMEX_LEADS_MOEX_GOLD.md` |
| — | H-0001 v0.2 seconds screen | 2026-11-02 | protocol only (pre-data) | pending | — | HL-0002 unopened | preregistration of two-month 1 s screen; v0.1 deferred | — | `H-0001_SECONDS_SCREEN_V0_2.md` |
| audit-explore-20261102 | H-0001 v0.2 audit + September-only exploration | 2026-11-02/03 | `3fbe051` (HEAD at audit) | v0.2 config unchanged (`6619460e…` CRLF / `ce91e94c…` LF) | seconds manifest `dc43c416…`; raw MOEX/COMEX as in P2B/P2C/P3 | HL-0002 October UNOPENED; September only | independent replication of discovery, sensitivity to day gate/cap, multi-scale 1 s–15 min, price-gap correction, crude cost check | exploratory (no category) | `H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md` |

Corrections are appended as new rows referencing the superseded run. Never delete an inconvenient run.
