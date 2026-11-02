# Copy-paste prompt for Claude Code — close P0, commit/push, then execute P1 with agents

Рабочая папка: `D:\project\moex-arbitrage-lab`.

Пользователь явно поручает Claude Code выполнить все изменения, cleanup, commits и pushes. Codex остаётся только архитектором/независимым reviewer и не должен коммитить, пушить или реализовывать stage. P0 code прошёл финальную независимую проверку Codex: targeted `104 passed`; guarded full gate `385 passed, 1 skipped`; coverage `97.76%`; ownership/recovery findings закрыты. Выполни Phase A полностью, затем — только после успешного push — переходи к Phase B/P1.

Не начинай P2/P3, lead-lag analysis, trading или платные запросы. Не объявляй P1 принятым самостоятельно: после выполнения остановись для review Codex/пользователем.

## Обязательный multi-agent подход

Пользователь требует систематически использовать Claude subagents и skills во всех следующих этапах. Зафиксируй это в `CLAUDE.md` и repository-local Claude configuration.

1. Добавь общий project skill `.claude/skills/parallel-stage-execution/SKILL.md`:
   - main agent владеет интеграцией, shared docs/status, provider/cost decisions и final gate;
   - минимум три параллельных безопасно разделённых workstreams плюс независимый read-only reviewer;
   - ни два агента не редактируют один файл;
   - public-source research, production code и synthetic tests можно параллелить;
   - provider calls, token/cost gates, raw publication, holdout access, commits и pushes выполняются последовательно main agent;
   - каждый subagent возвращает actual evidence/commands/artifacts, prose summary недостаточно.
2. Добавь stage skill `.claude/skills/p1-moex-zero-cost/SKILL.md` с P1 zero-charge, immutable raw, timestamp/provenance/QA и stop-gate правилами из `AGENTS.md` и P1 task.
3. Сохрани существующие profiles. Добавь:
   - `.claude/agents/moex-source-verification.md` — read-only official-source identifiers/calendar/archive/license/zero-cost verification;
   - `.claude/agents/moex-ingest-tests.md` — только synthetic fixtures/tests, без provider access и без production-file edits.
4. Для P1 используй максимально доступный безопасный параллелизм, ориентир — пять специализированных workstreams:
   - GDZ6 identifier/contract/session verification;
   - archive schema/license/rate-limit/zero-cost verification;
   - production adapter implementation;
   - synthetic parser/failure/QA tests;
   - independent read-only safety/reproducibility review.
5. Если новые profiles не подхватываются в текущей Claude session, запусти general-purpose subagents с теми же explicit boundaries; созданные profiles должны работать в следующих сессиях.

Главный агент обязан выдать каждому агенту точный scope и ownership. Не позволяй параллельных реальных probe/download requests: сеть и rate limit централизованы у main agent.

## Phase A — exact cleanup и формальное закрытие P0

Codex при прежнем незащищённом тесте создал один файл вне workspace. Пользователь явно разрешил Claude удалить только этот exact path:

```text
C:\Users\Aleksey\AppData\Roaming\uv\python\cpython-3.12.15-windows-x86_64-none\Lib\encodings\__pycache__\ascii.cpython-312.pyc
```

Ожидаемые свойства:

- regular file, не directory;
- 2575 bytes;
- creation и last-write `2026-11-02T15:52:17.2149890+03:00`;
- attributes `Archive`;
- не symlink/reparse point;
- `fsutil hardlink list` показывает ровно один literal path выше.

Перед удалением заново проверь все свойства. Если хотя бы одно не совпало — ничего не удаляй и верни blocker. Если совпало — удали только этот exact file literal-path operation; не удаляй `__pycache__`, interpreter tree или соседние файлы. Докажи отсутствие после удаления.

Также докажи, что четыре прежних exact msgpack paths отсутствуют:

```text
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\52e99d57eb652d4d.msgpack
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\55aed9fbc3fc0dea.msgpack
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\97d7aae16c3003c7.msgpack
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\92d9c6e269209859.msgpack
```

После cleanup:

1. Запусти guarded storage check; не запускай direct unguarded `uv`/Python.
2. Обнови `docs/PROJECT_STATUS.md`, `docs/MIGRATION_MANIFEST.md`, `docs/tasks/P0_BOOTSTRAP_AND_MIGRATION.md` и `CLAUDE.md`: P0 accepted; P1 user-authorized/current; P2 blocked.
3. Зафиксируй independent acceptance evidence: targeted `104 passed`, guarded full `385 passed, 1 skipped`, coverage `97.76%`, плюс exact cleanup.
4. Добавь multi-agent profiles/skills выше и этот handoff в stage history. Не меняй P0 implementation code.
5. Проверь repository hygiene/secrets и затем один guarded full gate после doc/Claude-config changes, потому что static hygiene tests сканируют repository text.

## Phase A — commit и push принадлежат Claude

Пользователь явно разрешил Claude сделать commit и push. Codex обнаружил: branch `main`, commits пока отсутствуют, `git remote -v` пуст.

- Проверь branch/status/remote самостоятельно.
- Не добавляй ignored cache/temp/logs/raw/artifacts/credentials.
- Не меняй legacy repository.
- После green gates создай initial commit с P0 scaffold, reviewed fixes, documentation и Claude agent/skill configuration.
- Не меняй global Git identity/config. Если identity отсутствует — верни blocker пользователю.
- Не придумывай remote URL, не создавай GitHub/GitLab repository и не добавляй remote без явного URL/решения пользователя.
- Если remote отсутствует, локальный commit разрешён, но затем остановись и запроси remote URL; **не начинай Phase B до успешного push**, поскольку пользователь задал порядок commit/push → next stage.
- Если remote существует, push current `main`, зафиксируй remote/branch/commit SHA и продолжай только после успешного push.

## Phase B — P1 MOEX/T-Bank one-day ingest

Начинай только после успешного Phase A push. Полностью прочитай `AGENTS.md`, `CLAUDE.md`, `docs/tasks/P1_MOEX_TRADE_DATA.md`, ADR-0001…0004, technical specification, storage/cost policies, H-0001 и unopened holdout ledger. Load skills `parallel-stage-execution` и `p1-moex-zero-cost`; запусти subagents по схеме выше.

### Stop gates до payload request

Main agent собирает и независимо сверяет:

- official `TICKER_CC`, `INSTRUMENT_UID`, venue/root/expiry и contract economics GDZ6;
- official archive URL/schema/field semantics/rate limit/license/retention/zero-charge evidence;
- October 2026 calendar и небольшой candidate-day list;
- механический выбор одного complete ordinary weekday без просмотра lead-lag outcomes;
- bounded probe plan, expected status/content type/size и stop conditions;
- подтверждение, что token не требуется; если требуется — stop, пользователь задаёт его локально позже;
- `recover_raw_publications()` preflight и обязательный `report.clean` before publication.

Используй primary/official sources. Если identity, calendar, license, zero-charge status или expected response неоднозначны — не загружай payload, верни `BLOCKED`. Не сканируй месяц; соблюдай 30 files/min/IP.

### Реальная сеть и acquisition

После green stop gates main agent последовательно выполняет только:

1. один bounded existence/status probe;
2. ровно одну approved daily archive acquisition при подтверждённой нулевой стоимости.

Никаких параллельных provider calls. Stop при auth, unexpected content, возможной оплате, HTML/error/partial/corrupt response. Не печатай и не сохраняй token.

### Implementation и QA

- Ownership — строго `docs/tasks/P1_MOEX_TRADE_DATA.md`.
- Raw bytes immutable/create-once через P0 protocol: manifest, checksum, marker, `verify_published`.
- Retry/race не перезаписывает raw.
- Deterministic `trade_v1` parsing с source file/hash/record/row provenance.
- Raw сохраняет все строки; primary eligibility — подтверждённый `TRADE_SOURCE=EXCHANGE`.
- Не трактуй `DIRECTION` как aggressor side без официальной семантики.
- Reconcile archive → CSV → parsed → eligible и каждую exclusion reason.
- Измерь timestamp semantics/quantum, nulls, ties, order, gaps, duplicates, corrections, sessions, first/last event и machine-readable support каждого preregistered lag bucket.
- Не запускай lead-lag analysis и не делай выводов о H-0001.
- Common contracts, lockfile, P2/P3/P4, H-0001 и holdout не меняй без отдельного interface blocker/approval.

### P1 tests, commit и push

Synthetic targeted tests first, bounded mocked failure/integration tests second, один guarded full gate after integration. Independent-review agent проверяет actual raw manifest/checksum/count reconciliation, task boundary, secret/storage/cost safety и claims.

Raw/generated/credentials остаются ignored и не попадают в Git. После green evidence пользователь разрешил Claude:

- обновить P1 status/evidence как `executed; awaiting Codex/user review`, не `accepted`;
- commit только P1 code/tests/config/docs без raw/generated artifacts;
- push current branch to configured remote;
- вернуть commit SHA и push evidence.

## Финальный ответ Claude

Верни отдельно Phase A и Phase B:

- exact cleanup checks/result;
- agent roster, parallel workstreams, ownership и reports каждого subagent;
- changed/committed files, ignored generated files, commits и push evidence;
- official source URLs/claims/retrieval timestamps и candidate-day decision;
- probe/acquisition commands, status/non-secret headers, bytes/SHA-256 and zero-charge evidence;
- raw relative path и manifest/checksum/marker/verification;
- parser/config/code hashes, reconciled counts и QA artifacts;
- timestamp/session/lag-support results;
- targeted/full gates, storage/secret/hygiene scans;
- deviations, unknowns, blockers и residual risks;
- new repo and unchanged legacy status.

Do not self-accept P1 and do not start P2/P3. Stop for Codex/user review.
