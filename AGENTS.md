# Repository instructions

## Scope

This repository is research-only. The primary hypothesis is COMEX/world market → MOEX GOLD. Do not claim that lead-lag exists before evidence is produced.

## Safety gates

- Never place orders, activate trading, open/fund accounts, or call a trading endpoint.
- Never make a request that can exceed free credit. A cost estimate and a recorded zero-charge decision are mandatory before any Databento download.
- Never request, print, persist, or commit real tokens. Read secrets only from environment variables and redact them in diagnostics.
- Keep every project-created file under `D:\project\moex-arbitrage-lab`; do not create project data on `C:`.
- Do not modify `D:\project\moex-event-alpha`. Read migration sources only with `git show`/`git ls-tree` at commit `068bbd8d86a935686d741ea0ef1949617f9f0cab`.
- Do not copy legacy code until the migration manifest is approved and P0 is explicitly authorized.

## Scientific rules

- Preserve the full source timestamp precision; never invent precision.
- Keep event, receive, and local-ingest timestamps separate. Missing event time stays missing.
- All as-of joins are backward-only and carry an explicit staleness bound.
- Analyze returns/price changes, not correlation of non-stationary price levels.
- Record observation counts, session overlap, timestamp ties, inter-trade gaps, stale fractions and exclusions.
- Pre-register holdout boundaries. Do not inspect holdout outcomes while tuning.
- Correct for the lag/method search and use block-aware inference and placebo controls.
- A trades-only signal is predictive evidence at most; execution feasibility requires later top-of-book and cost analysis.

## Change discipline

- Each task owns only the files listed in its task specification.
- Do not let parallel agents edit the same file.
- Return commands, exit codes, logs and artifact paths as evidence.
- Run tests for the changed area. Full integration is required after actual migration and before a research release, not after every documentation edit.
- Generated market data, caches, environments, credentials and large reports are never committed.

