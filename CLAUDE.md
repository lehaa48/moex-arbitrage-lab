# Claude Code handoff

Claude Code is the primary implementer after explicit user authorization. Codex owns architecture/review; the user owns stage transitions and every monetary decision.

## Current authorization and stop line

**Current state (2026-11-03, after October):** H-0001 v0.3 was registered and frozen, and October 2026 was opened **once** (user authorization 2026-11-03): v0.2 and v0.3 are both `SCREEN_POSITIVE`, combined decision `PROCEED` — `docs/experiments/H-0001_OCTOBER_CONFIRMATION_RESULT.md`. October is now training evidence (HL-0002-OPENED, HL-0003-OPENED). **Stop line:** do not run any further `confirm`/`calibrate`, start order-book recording, request or handle a token, or make any provider request without explicit user authorization for that stage. Leave `src/moex_arbitrage_lab/research/*.py`, `src/moex_arbitrage_lab/research_v03/*.py` and their configs byte-unchanged so the recorded artifacts stay reproducible; new work goes into new modules. The next candidate stage is read-only forward GDZ6 order-book recording via the T-Bank Invest API (read-only token set locally by the user; agents never see it).

**Previous state (2026-11-03, before October):** H-0001 v0.2 September discovery frozen (`h*=1 s`); audit and September-only exploration in `docs/experiments/H-0001_SEPTEMBER_AUDIT_AND_EXPLORATION.md`.

**Earlier task (2026-11-02, ADR-0005): the H-0001 v0.2 two-month one-second screen** — `docs/tasks/CLAUDE_SECONDS_SCREEN_PIVOT_PROMPT.md`, protocol `docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md`. It supersedes the one-day P1 route described in the next paragraph (kept as history). The only mandatory external stop is Databento Step B (explicit approval of the exact estimate); Step A needs the user-set `DATABENTO_API_KEY`.

**P0 is accepted** (independent Codex acceptance 2026-11-02 after two corrective passes). **P1 is user-authorized and the current stage**, executed per `docs/tasks/CLAUDE_P0_CLOSE_AND_P1_PROMPT.md` Phase B, which starts only after the Phase A commit has been pushed successfully to a configured remote. **P2 is blocked** (separate authorization and cost gate). Do not start P2/P3, lead-lag analysis, trading or any paid request. After P1 evidence, stop for Codex/user review; never self-accept a stage.

Claude Code performs all changes, cleanup, commits and pushes for authorized stages. Never change global Git identity/config, invent a remote URL or create a hosted repository; ask the user instead.

## Multi-agent requirement

The user requires systematic use of Claude subagents and skills in every stage from P1 on:

- load `.claude/skills/parallel-stage-execution/SKILL.md` (roles, ownership, evidence contract) and the stage skill (P1: `.claude/skills/p1-moex-zero-cost/SKILL.md`);
- run at least three parallel workstreams with disjoint file ownership plus one independent read-only reviewer; no two agents edit the same file;
- the main agent alone owns integration, shared docs/status, provider/cost/token decisions, real network calls (sequential), raw publication, holdout access, the final gate, commits and pushes;
- every subagent returns actual evidence (files + SHA-256, commands + exit codes, source URLs + retrieval times), never prose only;
- profiles live in `.claude/agents/`; if one is not loaded in a session, use a general-purpose subagent with the same explicit boundaries.

## Authorized sequence

1. On explicit approval, execute only [P0](docs/tasks/P0_BOOTSTRAP_AND_MIGRATION.md). — done; accepted 2026-11-02.
2. Return the evidence bundle and stop for review.
3. After P0 acceptance, P1 and P2 may run in parallel in disjoint file areas. — P1 authorized and current; P2 still requires its own authorization.
4. P3 integrates one pre-declared common trading day.
5. P4 starts only after the vertical slice and five-day stability review.

## Required implementation behavior

- Follow `AGENTS.md`, ADRs, cost gate and storage policy.
- Treat the task document as a file-ownership boundary.
- Reuse only items marked `adapt` or `migrate` in `docs/MIGRATION_MANIFEST.md`; preserve provenance and verify source identity.
- Never use the legacy working tree as input; use the pinned commit.
- No continuous-contract black box, forward-looking interpolation, unlimited forward fill, or silent timestamp substitution.
- No PASS threshold may be invented after seeing results.

