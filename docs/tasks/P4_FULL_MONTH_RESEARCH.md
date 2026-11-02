# P4 — Full-month H‑0001 research

Status: blocked by P3 review, five-day discovery pilot, frozen protocol and explicit stage approval.

> **2026-11-02 (ADR-0005, append-only):** **deferred.** The full v0.1 study (sub-second lags, trade-level data) is reconsidered only if the v0.2 seconds screen returns `SCREEN_POSITIVE` and the user approves a next stage.

## Objective

Run the preregistered H‑0001 on October 2026 or another justified completed month using one explicit GDZ6/GCZ6 pairing and whole-day chronological holdout.

## Preconditions

- Complete-day coverage/licensing and zero-cost acquisition plan are accepted.
- Five discovery days have fixed parsing, clocks, sampling, stale rules and primary model.
- Holdout ledger records dates, input/code/config hashes, multiplicity family, block rules, placebos and decision rubric before outcomes are opened.
- Contract/session map excludes or separately labels exceptional/expiry/roll days; no continuous-future black box.

## Execution

Acquire only after per-request cost gates. Run discovery/validation, freeze code/config, then open the holdout once. Report day-level and pooled effects, simultaneous confidence bands, adjusted inference, leave-one-day-out, asynchronous estimators, staleness/activity/time-of-day sensitivity, reverse/impossible-time/day-mismatch/circular-shift placebos and baseline prediction comparison.

## Acceptance and evidence

All eligible/excluded days and observations reconcile; holdout access is audited; no tuning uses holdout; all deviations are appended, never overwritten; result uses exactly one decision category from H‑0001 and separates predictive evidence from executability. Return immutable run manifest, full artifact index, costs, tests and review sign-off.

