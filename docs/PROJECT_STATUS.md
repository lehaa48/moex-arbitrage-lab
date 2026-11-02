# Project status

Last updated: 2026-11-02 (Asia/Riyadh)

## Current stage

**Current (2026-11-02, ADR-0005): H-0001 v0.2 two-month one-second screen** (`docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md`, task `docs/tasks/CLAUDE_SECONDS_SCREEN_PIVOT_PROMPT.md`). The one-day P1 → P3 → five-day → P4 route is deferred, not deleted. Provider contact so far: one T-Bank HEAD (2026-10-06 → 200, `logs/tbank/requests.jsonl`); no trade payload downloaded yet; no credentials used. Databento Step A is blocked until the user sets `DATABENTO_API_KEY` locally; Step B additionally needs explicit approval of the exact estimate.

Previous text (kept): **P0 accepted** on 2026-11-02 by independent Codex review after two bounded corrective passes. **P1 (MOEX/T‑Bank one-day ingest) is user-authorized and current**; its execution (Phase B of `docs/tasks/CLAUDE_P0_CLOSE_AND_P1_PROMPT.md`) starts only after the Phase A initial commit is pushed to a configured remote. **P2 is blocked.** No market data downloaded; no market-data/provider endpoint contacted; no credentials used; no importers implemented.

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
| P1 MOEX ingest (one-day route) | superseded by v0.2 screen 2026-11-02; adapter reused | one raw daily sample and QA, zero charge |
| P2 COMEX ingest (`trades`) | deferred; v0.2 uses `ohlcv-1s` | cost estimate then one raw sample, zero charge |
| P3 one-day slice | deferred (ADR-0005) | normalized/aligned data and report |
| Five-day pilot | deferred (ADR-0005) | per-day stability report |
| Full month | deferred (ADR-0005) | preregistered month report |
| **H-0001 v0.2 seconds screen** | **current**: protocol frozen; MOEX acquisition pending; Databento Step A blocked (no key) | two-month raw (both venues), 1 s series, September `h*`, October confirmation, one of four categories |

## Stage history

| Date | Event |
|---|---|
| 2026-11-02 | User authorized P0 (`docs/tasks/CLAUDE_P0_PROMPT.md`); Claude Code executed it. |
| 2026-11-02 | Codex review: changes required; corrective pass 1 (`CLAUDE_P0_REVIEW_FIX_PROMPT.md`) executed. |
| 2026-11-02 | Codex review: two ownership/recovery findings; corrective pass 2 (`CLAUDE_P0_REVIEW_FIX2_PROMPT.md`) executed. |
| 2026-11-02 | Codex final acceptance of P0. Handoff `CLAUDE_P0_CLOSE_AND_P1_PROMPT.md`: exact cleanup, P0 closure, multi-agent skills/profiles, initial commit/push by Claude, then P1 with parallel subagents. |
| 2026-11-02 | Initial commit `9a28c34` pushed to `origin/main` (`https://github.com/lehaa48/moex-arbitrage-lab.git`). P1 one-day work started: source verification, adapter, one HEAD probe (2026-10-06 → 200, gzip CSV). |
| 2026-11-02 | User pivot (`CLAUDE_SECONDS_SCREEN_PIVOT_PROMPT.md`, ADR-0005) before any payload/outcome: two-month one-second screen; v0.1 route deferred. |

## Known external decisions

- User approved P0 on 2026-11-02 and P1 via the close/P1 handoff; neither authorizes P2 or any paid request.
- Claude Code makes commits and pushes; the user supplies Git identity and the remote URL. Global Git config is never changed by agents.
- User later creates any required T‑Bank/Databento account and sets tokens locally; agents never request, print or persist tokens.
- User must approve any non-zero cost. Free credit alone is not spending authority.
