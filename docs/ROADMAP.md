# Roadmap

1. **Stage A — now:** freeze scope, sources, scientific protocol, storage/cost rules and migration inventory.
2. **P0:** adapt only approved generic infrastructure; prove fail-closed storage and research-only runtime.
3. **P1 ∥ P2:** independently obtain and validate one day of MOEX and COMEX trades after all gates pass.
4. **P3:** choose the common day from preregistered availability/liquidity rules, normalize, intersect sessions, run timestamp QA and exploratory lead-lag report.
5. **Five-day pilot:** test sign/shape stability and sampling sensitivity per day.
6. **P4 full month:** October 2026 or another justified completed month; fixed contracts, no implicit roll; locked holdout.
7. **3–6 months:** explicit contract map and roll regimes.
8. **Execution feasibility:** only after predictive evidence; add top-of-book, costs, latency and realistic fills.

The shortest path is P0 → P1/P2 in parallel → P3. Documentation or framework work that does not remove a gate on that path is deferred.

## Update 2026-11-02 — ADR-0005 (append-only)

Steps 3–6 above are **deferred** for the current screening decision. Active route: P0 (accepted) → **H-0001 v0.2 two-month one-second screen** (`docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md`): MOEX T-Bank archive Sep–Oct 2026 + COMEX Databento `ohlcv-1s` (exact-estimate approval gate) → September discovery → frozen `h*` → October confirmation → one of four screen categories. A `SCREEN_POSITIVE` result only permits designing a trade-level/top-of-book stage; `NO_SCREEN_SIGNAL` stops the project until a new user decision.

