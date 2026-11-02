# ADR-0004: Explicit contracts and staged evidence

Status: accepted — 2026-11-02

## Decision

H‑0001 uses explicit GDZ6 and GCZ6 candidates and a date-specific session intersection. No continuous-futures black box or implicit roll is allowed. Evidence advances from one-day plumbing, to five-day discovery, to locked full-month holdout, and only then to possible execution feasibility.

## Consequences

One-day/five-day results cannot confirm the hypothesis. Roll/expiry regimes and top-of-book costs are separate later stages. Contract metadata and source coverage are gates, not assumptions.

