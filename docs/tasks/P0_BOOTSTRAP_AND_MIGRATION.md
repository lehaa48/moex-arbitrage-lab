# P0 — Bootstrap and approved minimal migration

Audience: Claude Code. Status: **accepted on 2026-11-02** by independent Codex review after two corrective passes (`CLAUDE_P0_REVIEW_FIX_PROMPT.md`, `CLAUDE_P0_REVIEW_FIX2_PROMPT.md`). Acceptance evidence: targeted `104 passed`; guarded full gate `385 passed, 1 skipped`; coverage `97.76%`; exact out-of-root cleanup recorded in `docs/PROJECT_STATUS.md`. Next stage: P1 — user-authorized and current; execution starts only after the initial commit is pushed. P2 blocked.

## Objective

Create the smallest reproducible, research-only Python scaffold that unblocks two independent source adapters. Adapt only approved generic infrastructure from the pinned legacy commit; do not implement or call market-data importers.

## File ownership

P0 may create/edit: `pyproject.toml`, `.python-version`, `uv.lock`, `src/moex_arbitrage_lab/{contracts,settings}/`, `scripts/`, `tests/{contract,unit}/`, `.pre-commit-config.yaml`, `.vscode/tasks.json`, and the actual-checksum/evidence cells in `docs/MIGRATION_MANIFEST.md` and `docs/PROJECT_STATUS.md`.

Do not edit experiment design, source-matrix claims, P1/P2 adapters, or legacy files. Coordinate before changing any other file.

## Inputs

- `AGENTS.md`, ADRs and storage/cost policies;
- approved rows M‑001 through M‑009 and M‑019 in the migration manifest;
- source objects at legacy commit `068bbd8d86a935686d741ea0ef1949617f9f0cab` only.

## Work

1. Create a Python 3.12 package and fresh uv lock. Do not copy the old lock and do not add T‑Invest/Databento clients.
2. Adapt fail-closed environment/root checks for exactly `D:\project\moex-arbitrage-lab`; keep cache/temp/venv under the project; reject escapes, reparse/junction surprises and disk `C:` for project storage.
3. Implement a research runtime with trading permanently disabled and no order/trading capability.
4. Define versioned Pydantic or typed contracts for acquisition manifest and canonical `trade_v1`; distinguish nullable event/receive timestamps from local ingest time.
5. Provide checksum and create-once raw-publication utilities sufficient for later daily files. Do not port the old streaming segment/replay graph.
6. Adapt targeted tests from legacy failure scenarios. Add synthetic tests for timestamp non-substitution, path escape, immutable raw collision, secret redaction and trading fail-closed.
7. Make the VS Code `bootstrap environment`, `verify storage` and `run relevant tests` tasks operational. Other data tasks may remain explicit P1/P2/P3 interfaces but must fail clearly if their commands are absent.
8. Update migration rows with exact source blob OID, source SHA‑256, destination, target SHA‑256 and test name.

## Explicit exclusions

No market-data/provider calls, market data, tokens, `.env` values, broker SDK, live recorder, source adapter, normalized Parquet implementation, lead-lag statistics, paid dependency/service, or modification of the old repo. Network access is permitted only to the standard Python package index when strictly required to resolve/install the declared free P0 development dependencies; record it in evidence and do not contact T‑Bank, MOEX data endpoints, CME or Databento.

## Acceptance criteria

- Fresh clone/bootstrap is reproducible with the new lock and uses only new-project paths.
- Tests prove fail-closed root, traversal/reparse escape, trading mode, secret output and timestamp semantics.
- Raw utility refuses overwrite and emits SHA-256/provenance.
- `trade_v1` contains the fields specified in the technical spec and accepts missing source timestamps only as null.
- Default quality gate is offline and passes; all generated state is under the workspace.
- No rejected/deferred legacy module or legacy namespace remains.

## Evidence bundle

Return: exact commands and exit codes; `git diff --stat`; dependency list; path-resolution report; test names/results; quality-gate log; source/target checksum table; scan proving no secret/data artifacts; documented deviations. Stop after evidence—do not begin P1/P2.

