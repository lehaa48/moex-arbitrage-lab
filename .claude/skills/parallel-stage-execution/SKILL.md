---
name: parallel-stage-execution
description: Run any authorized MOEX Arbitrage Lab stage (P1 onward) with parallel Claude subagents under strict file ownership. Use at the start of every stage task, before spawning agents, and whenever splitting work between agents.
---

# Parallel stage execution

The user requires systematic use of Claude subagents for every stage. This skill defines who may do what. It never widens a stage's authorization: the active task document and `AGENTS.md` still decide scope.

## Roles

**Main agent (the session you are in)** owns, exclusively and sequentially:

- integration of agent outputs and every shared file: `CLAUDE.md`, `docs/PROJECT_STATUS.md`, `docs/MIGRATION_MANIFEST.md`, task/status docs, `pyproject.toml`, `uv.lock`, common contracts;
- provider, cost and token decisions; every real network probe or download (one at a time, within provider rate limits);
- raw publication (`publish_raw`, `recover_raw_publications` preflight with `report.clean`), holdout access, Git commits and pushes;
- the single guarded full quality gate per integration and the final evidence bundle.

**Subagents** each receive, in the prompt, an exact scope: the files they may create/edit (or "read-only"), the commands they may run, what is forbidden, and the evidence they must return.

## Required shape

1. At least **three parallel workstreams with disjoint file ownership**, plus **one independent read-only reviewer** (`independent-review` profile) that does not edit files.
2. **No two agents edit the same file.** Write the ownership table (agent → files) before spawning. If two workstreams need one file, the main agent owns it and integrates.
3. Safe to parallelize: public-source/documentation research, production code in disjoint modules, synthetic fixtures/tests, read-only review.
4. Never parallel, main agent only: provider calls (including "just a HEAD"), token or cost gates, raw publication, holdout ledger access, commits, pushes.
5. Subagents never contact a market-data provider, never read or print tokens, never write outside `D:\project\moex-arbitrage-lab`, and run `uv`/Python only through the guarded environment (`scripts/project_env.ps1` or `. scripts/project_env.sh`).

## Evidence contract

Every subagent returns **actual evidence**, not a prose summary:

- files created/changed with SHA-256;
- exact commands with exit codes and the relevant log lines or log paths;
- for research: source URL, retrieval timestamp (UTC), the quoted claim, and confidence/unknowns;
- for review: findings by severity with file:line or artifact/command evidence, and explicit PASS/FAIL per acceptance criterion.

The main agent re-verifies key claims itself (checksums, counts, test results) before integrating, and records the agent roster, ownership and reports in the stage evidence.

## Profiles

Prefer repository profiles in `.claude/agents/` (`moex-source-verification`, `moex-ingest`, `moex-ingest-tests`, `independent-review`, ...). If a profile is not loaded in the current session, launch a general-purpose subagent and paste the same boundaries from the profile file into its prompt.

## Stop rules

Stop and report `BLOCKED` instead of improvising when: ownership would overlap, a gate needs a user decision (cost, token, identity, remote, ambiguous source), or an agent's evidence cannot be reproduced by the main agent.
