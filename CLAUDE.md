# Claude Code handoff

Claude Code is the primary implementer after explicit user authorization. Codex owns architecture/review; the user owns stage transitions and every monetary decision.

## Current authorization and stop line

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

