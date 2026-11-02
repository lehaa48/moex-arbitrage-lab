# Copy-paste prompt for Claude Code — resume Phase A, commit/push, execute P1

Рабочая папка: `D:\project\moex-arbitrage-lab`.

Продолжи текущую работу с фактического состояния repository. Полностью соблюдай `AGENTS.md`, `CLAUDE.md` и `docs/tasks/CLAUDE_P0_CLOSE_AND_P1_PROMPT.md`. Не повторяй уже выполненный cleanup и не перезапускай без причины завершённые Phase A операции: сначала проверь их evidence/state. Все изменения, cleanup, commits и pushes выполняет Claude; Codex остаётся независимым reviewer.

## Текущий checkpoint

- P0 принят; exact `.pyc` и четыре msgpack-файла отсутствуют.
- Phase A docs/status/skills/agent profiles подготовлены.
- Последний guarded full gate: `385 passed, 1 skipped`, coverage `97.76%`.
- Последний targeted hygiene после review fixes: `34 passed`.
- Initial commit ещё не создан.
- Branch: `main`.
- Legacy repo не трогать.
- P1 не начат и начинается только после successful initial push.

## Git identity — пользователь разрешил

Используй identity прошлого проекта, но задай её **только локально в этом repository**, не меняя global/system config:

```text
user.name = Aleksey
user.email = lehaa48@gmail.com
```

Проверь effective local values перед commit и верни их в evidence.

## Новый remote — отдельный repository в том же аккаунте

Не используй старый `lehaa48/moex-event-alpha` и никогда не push туда. Новый проект должен иметь отдельную историю:

```text
owner: lehaa48
repository: moex-arbitrage-lab
visibility: private
remote name: origin
remote URL: https://github.com/lehaa48/moex-arbitrage-lab.git
branch: main
```

Пользователь разрешает использовать/создать именно этот новый private repository. Но соблюдай secret/tool safety:

- сначала проверь, существует ли remote;
- если private repository уже создан и доступен через существующую Git authentication, добавь `origin` и продолжай;
- если repository не существует, создавай его только через уже установленный и уже authenticated официальный инструмент, не извлекая/не печатая credentials и не меняя visibility;
- не читай Git Credential Manager secret, не проси вставлять token в чат/команду, не сохраняй token и не используй legacy remote как fallback;
- не устанавливай system-wide GitHub CLI и не создавай out-of-root state ради этого шага;
- если безопасного authenticated инструмента нет (ранее `gh` отсутствовал), остановись только с blocker: пользователь создаёт пустой private repository `lehaa48/moex-arbitrage-lab` в GitHub UI. Не придумывай другой URL и не начинай P1 до successful push.

Пустой remote должен быть без README, `.gitignore` и license, чтобы initial history принадлежала этому project.

## Перед initial commit

1. Добавь `*.zst` в `.gitignore` как defense-in-depth для compressed raw files.
2. Запусти targeted repository-hygiene/secret tests через guarded environment. Полный gate уже был выполнен после Phase A; повторяй его только если изменился executable code/config или targeted test обнаружил проблему.
3. Сформируй exact commit set через `git ls-files --others --exclude-standard` / staged diff.
4. Убедись, что в commit нет `data/`, `artifacts/`, `cache/`, `tmp/`, `logs/`, `.venv`, `.env`, credentials, `*.pyc`, market data или generated reports.
5. Проверь accepted P0 hashes и unchanged legacy HEAD/status.

## Initial commit и push — выполняет Claude

- Настрой repository-local identity выше.
- Добавь только проверенный project commit set.
- Создай initial commit, например: `chore: bootstrap reproducible arbitrage research lab`.
- Push `main` в `origin` без force.
- Верни commit SHA, `git status`, remote URL, upstream branch и push result.
- При любой auth/permission/remote ambiguity остановись; не ослабляй privacy и не применяй force/reset/rewrite.

## После successful push — сразу Phase B / P1

После push выполни полностью Phase B из `docs/tasks/CLAUDE_P0_CLOSE_AND_P1_PROMPT.md` и `docs/tasks/P1_MOEX_TRADE_DATA.md`.

Обязательно загрузи skills:

- `.claude/skills/parallel-stage-execution/SKILL.md`;
- `.claude/skills/p1-moex-zero-cost/SKILL.md`.

Используй максимально доступный безопасный parallel agent roster с disjoint ownership:

1. official GDZ6 identifier/contract/session verification;
2. archive schema/license/rate-limit/zero-cost verification;
3. `moex-ingest` production implementation;
4. `moex-ingest-tests` synthetic fixtures/tests;
5. `independent-review` read-only audit.

Если repository profiles не hot-loaded в этой session, используй general-purpose subagents с теми же границами. Главный агент один выполняет реальные provider probes/download, cost/token decisions, raw publication, integration, commits и pushes. Никаких параллельных provider calls.

До payload request должны быть доказаны official GDZ6 identity, archive terms/schema/rate limit/zero-charge status, October 2026 calendar, outcome-independent candidate-day rule, expected response и отсутствие auth requirement. При ambiguity/auth/possible charge — `BLOCKED`, ничего не скачивать.

После green gates: один bounded probe, одна approved zero-charge daily archive, immutable raw publication, deterministic `trade_v1`, full row reconciliation и timestamp/session/source QA. Не запускать lead-lag analysis и не начинать P2/P3.

После P1 integration:

- targeted tests, mocked failures, один guarded full gate;
- independent read-only review фактических artifacts/checksums/counts;
- status `P1 executed; awaiting Codex/user review`, не accepted;
- commit только code/tests/config/docs, без raw/generated artifacts;
- push `main` в `origin` без force;
- вернуть agent reports, source evidence, commands/exit codes, hashes/counts/QA, commit SHA и push evidence;
- остановиться для Codex/user review.

