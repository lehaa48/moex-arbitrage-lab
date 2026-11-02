# Holdout ledger

The ledger is append-only. Dates are UTC unless venue trading dates are explicitly named.

| Entry | Hypothesis/version | Candidate universe | Training | Validation | Holdout | Frozen config hash | Opened at/by | Reason/change |
|---|---|---|---|---|---|---|---|---|
| HL-0001 | H-0001 v0.1 | candidate October 2026, GDZ6/GCZ6 | not assigned | not assigned | **UNOPENED** | — | — | Boundaries wait for verified complete-day availability; outcomes must not be inspected first. |
| HL-0002 | H-0001 v0.2 seconds screen | `GDZ6_SPBFUT` (MOEX) / `GCZ6` `ohlcv-1s` (COMEX), regular weekday sessions | 2026-09-01 … 2026-09-30 (discovery) | — (none; h* chosen on discovery) | 2026-10-01 … 2026-10-31 (locked confirmation) **UNOPENED** | pending: recorded with code commit and `h*` before opening | — | Registered 2026-11-02 before any trade payload or outcome (ADR-0005). HL-0001 v0.1 is deferred, not opened. |

Opening a holdout requires a frozen code commit, config hash and explicit record here. If the holdout influences a redesign, that period becomes training evidence and a new future holdout must be registered.

