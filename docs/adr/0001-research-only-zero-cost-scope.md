# ADR-0001: Research-only, zero-cost scope

Status: accepted — 2026-11-02

## Decision

The system has no order-entry capability and accepts no paper/live trading mode. Cash authorization is 0 RUB/0 USD; metered data requires a free cost estimate, a hard zero-charge decision and explicit user approval. Trades-only findings are labeled predictive research, never executable profit.

## Consequences

P0 excludes broker SDKs and credentials. Any non-zero data option or execution study is a later user-approved ADR/stage. Fail-closed behavior is preferred to automatic fallback.

