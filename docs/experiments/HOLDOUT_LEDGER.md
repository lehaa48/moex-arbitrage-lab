# Holdout ledger

The ledger is append-only. Dates are UTC unless venue trading dates are explicitly named.

| Entry | Hypothesis/version | Candidate universe | Training | Validation | Holdout | Frozen config hash | Opened at/by | Reason/change |
|---|---|---|---|---|---|---|---|---|
| HL-0001 | H-0001 v0.1 | candidate October 2026, GDZ6/GCZ6 | not assigned | not assigned | **UNOPENED** | — | — | Boundaries wait for verified complete-day availability; outcomes must not be inspected first. |

Opening a holdout requires a frozen code commit, config hash and explicit record here. If the holdout influences a redesign, that period becomes training evidence and a new future holdout must be registered.

