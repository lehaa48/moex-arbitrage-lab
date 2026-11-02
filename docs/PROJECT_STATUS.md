# Project status

Last updated: 2026-11-02 (Asia/Riyadh)

## Current stage

**P0 accepted** on 2026-11-02 by independent Codex review after two bounded corrective passes. **P1 (MOEX/T‑Bank one-day ingest) is user-authorized and current**; its execution (Phase B of `docs/tasks/CLAUDE_P0_CLOSE_AND_P1_PROMPT.md`) starts only after the Phase A initial commit is pushed to a configured remote. **P2 is blocked.** No market data downloaded; no market-data/provider endpoint contacted; no credentials used; no importers implemented.

## P0 acceptance evidence

- Independent Codex acceptance: targeted `104 passed`; guarded full gate `385 passed, 1 skipped`; coverage `97.76%`; ownership/recovery findings closed.
- Exact out-of-root cleanup (all user-authorized, literal-path, each property re-verified before deletion; neighbours unchanged):
  - `C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\{52e99d57eb652d4d,55aed9fbc3fc0dea,97d7aae16c3003c7,92d9c6e269209859}.msgpack` — absent;
  - `C:\Users\Aleksey\AppData\Roaming\uv\python\cpython-3.12.15-windows-x86_64-none\Lib\encodings\__pycache__\ascii.cpython-312.pyc` (2575 B, created/written 2026-11-02T15:52:17.2149890+03:00, Archive, one hard link) — removed 2026-11-02 ~16:32 +03:00, absent; its 15 neighbours and the directory unchanged.
- Guarded storage check after cleanup: PASSED.

## Gate state

| Gate | State | Exit evidence |
|---|---|---|
| A0 planning | accepted for P0 | documents, ADRs, source matrix, migration manifest |
| P0 bootstrap/migration | **accepted 2026-11-02** | isolated environment, storage guard, migrated tests, ownership-safe raw publication/recovery, evidence above and in `docs/MIGRATION_MANIFEST.md` |
| P1 MOEX ingest | **user-authorized; current**; execution waits for the Phase A push | one raw daily sample and QA, zero charge |
| P2 COMEX ingest | blocked: separate authorization + account/cost gate | cost estimate then one raw sample, zero charge |
| P3 one-day slice | blocked by P1/P2 | normalized/aligned data and report |
| Five-day pilot | blocked by P3 review | per-day stability report |
| Full month | blocked by stage approval | preregistered month report |

## Stage history

| Date | Event |
|---|---|
| 2026-11-02 | User authorized P0 (`docs/tasks/CLAUDE_P0_PROMPT.md`); Claude Code executed it. |
| 2026-11-02 | Codex review: changes required; corrective pass 1 (`CLAUDE_P0_REVIEW_FIX_PROMPT.md`) executed. |
| 2026-11-02 | Codex review: two ownership/recovery findings; corrective pass 2 (`CLAUDE_P0_REVIEW_FIX2_PROMPT.md`) executed. |
| 2026-11-02 | Codex final acceptance of P0. Handoff `CLAUDE_P0_CLOSE_AND_P1_PROMPT.md`: exact cleanup, P0 closure, multi-agent skills/profiles, initial commit/push by Claude, then P1 with parallel subagents. |

## Known external decisions

- User approved P0 on 2026-11-02 and P1 via the close/P1 handoff; neither authorizes P2 or any paid request.
- Claude Code makes commits and pushes; the user supplies Git identity and the remote URL. Global Git config is never changed by agents.
- User later creates any required T‑Bank/Databento account and sets tokens locally; agents never request, print or persist tokens.
- User must approve any non-zero cost. Free credit alone is not spending authority.
