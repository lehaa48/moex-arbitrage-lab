# Copy-paste prompt for Claude Code — P0 review corrections

Рабочая папка: `D:\project\moex-arbitrage-lab`.

Codex независимо проверил первый P0 pass. Вердикт: **CHANGES REQUIRED; P0 не принят**. Пользователь согласовал корректирующий pass. Исправь только перечисленные ниже P0 findings, повтори evidence и остановись. P1/P2, provider APIs и market data по-прежнему запрещены.

## Перед изменениями

1. Прочитай полностью `AGENTS.md`, `CLAUDE.md`, `docs/tasks/P0_BOOTSTRAP_AND_MIGRATION.md`, `docs/TECHNICAL_SPECIFICATION_V0_1_RU.md`, ADR-0001…0004, `docs/STORAGE_AND_SECRETS_POLICY.md`, `docs/COST_AND_PURCHASE_POLICY.md` и этот prompt.
2. Зафиксируй новый repo status и legacy repo status/HEAD. Не clean/reset/commit/push.
3. Не меняй H‑0001, source matrix, holdout ledger, P1–P4 или legacy repository.

## Finding 1 — точечно устранить out-of-root state

Предыдущий запуск `uv python list` создал на `C:` три файла, подтверждённые Codex по creation time 2026-11-02 14:46:28–29:

```text
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\52e99d57eb652d4d.msgpack  (1988 bytes)
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\55aed9fbc3fc0dea.msgpack  (1586 bytes)
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\97d7aae16c3003c7.msgpack  (1609 bytes)
```

Пользователь согласовал корректирующий pass. Перед удалением повторно проверь **каждый exact literal path**, что это обычный файл, а не link/reparse point, размер совпадает, creation/last-write относятся к указанному запуску и путь находится ровно в указанном каталоге. Затем удали только эти три файла через literal-path operation. Не удаляй каталог и никакие соседние/shared cache-файлы. Если хотя бы одна проверка не совпала — ничего на `C:` не удаляй, верни blocker.

После удаления докажи отсутствие этих трёх exact paths. Не запускай команды, которые снова пишут в default uv cache. Все uv-команды — только после guarded environment.

## Finding 2 — сделать raw publication логически crash-atomic

Текущий `publish_raw` последовательно создаёт payload/manifest/checksum final names и rollback делает только при перехваченном исключении. Kill/power loss оставляет partial publication; write/fsync failure внутри `_write_staging` также может оставить `.part`.

Исправь минимально, без переноса legacy streaming/replay subsystem:

- определи явную commit-marker семантику: publication считается видимой/валидной только после одной последней атомарной операции над commit marker;
- payload, manifest и checksum до commit остаются uncommitted; readers/`verify_published` обязаны требовать commit marker;
- final commit marker должен создаваться атомарно и create-once на том же filesystem;
- добавь bounded recovery/cleanup для orphaned staging/uncommitted artifacts этого publisher, не затрагивающий чужие/pre-existing файлы;
- `_write_staging` обязан удалить собственный partial file при write/fsync/close failure;
- failure финальной verification не должен оставлять publication, выглядящую committed;
- не называй физически атомарным то, что является protocol-level/logical atomicity; синхронизируй docstring и evidence с точной гарантией;
- raw payload по-прежнему immutable/create-once; collision никогда не перезаписывается.

Добавь tests как минимум для:

1. failure каждого staging write/fsync;
2. failure между каждым publish step;
3. abrupt subprocess termination до commit marker;
4. committed publication видима и верифицируется;
5. partial publication не считается валидной и recovery удаляет только собственные orphan artifacts;
6. concurrent/pre-existing target остаётся неизменным;
7. verification failure не оставляет committed state.

Если буквальное требование из technical spec невозможно при раздельных каталогах, зафиксируй именно logical atomicity через commit marker и предложи минимальную уточняющую формулировку в evidence; не перепроектируй storage layout без review.

## Finding 3 — убрать ложный запрет равных timestamps

В `TradeV1` удали запрет `event_ts_utc_ns == local_ingest_ts_utc_ns`. Равенство значений само по себе не доказывает подмену и может быть легитимным при грубой точности часов.

Сохрани правила:

- missing event time остаётся `None`;
- `event_ts_type` не может обозначать local/ingest/download time;
- adapters позднее обязаны явно маппить source semantics.

Добавь positive test: равные event/ingest timestamps принимаются при подтверждённом source event type. Existing tests отсутствующего event timestamp и запрещённой ingest semantics должны продолжать проходить.

## Finding 4 — усилить acquisition manifest

Добавь и протестируй:

- `retrieval_completed_utc_ns >= retrieval_started_utc_ns`;
- explicit `canonical_contract` в том же формате `<VENUE>:<ROOT>:<YYYY-MM-DD>` и согласование venue/expiry. Если для manifest нужен expiry, добавь его версионированно либо вынеси общий validated type так, чтобы continuous/ambiguous identifiers были невозможны;
- canonical deterministic JSON независимо от insertion order в `request_parameters` и `response_metadata`;
- deterministic round-trip/hash test для различного порядка одинаковых mapping entries.

Не ослабляй secret redaction и strict validation.

## Подтверждённые архитектурные решения

- Формат canonical contract `<VENUE>:<ROOT>:<EXPIRY>` подтверждён.
- `flags` как tuple строк подтверждён.
- Strict enum objects с явным adapter mapping подтверждены.
- `estimated_cost >= 0` допустим как metered usage, потенциально покрытый credit, но не как разрешение денежного списания.
- Успешный acquisition manifest может иметь только `FREE_CONFIRMED`; rejected estimates остаются в отдельном cost ledger.
- Для P2 позже потребуются отдельные доказательства available credit, `cash_charge == 0` и hard zero-charge boundary. P2 сейчас не реализуй.

## Разрешённые файлы

Изменяй только файлы, необходимые для четырёх findings: соответствующие P0 contracts/settings, их tests, точные evidence/checksum cells в `docs/MIGRATION_MANIFEST.md` и P0 status/evidence в `docs/PROJECT_STATUS.md`. Если нужен новый P0 helper в уже разрешённых `contracts/` или `settings/`, объясни его необходимость. Не расширяй platform scope.

## Повторная проверка

1. Запусти targeted tests новых failure scenarios.
2. Запусти полный offline P0 quality gate ровно один раз после targeted green.
3. Повтори storage/hygiene scans.
4. Проверь target SHA-256 и обнови migration evidence.
5. Докажи, что три exact cache paths на `C:` отсутствуют и новые out-of-root записи не появились.
6. Подтверди, что legacy status/HEAD не изменились, market data/credentials отсутствуют, P1/P2 не начаты.

## Финальный ответ

Верни findings → fixes mapping, точные изменённые файлы, команды/exit codes, targeted/full test results, новые target SHA-256, crash-atomic protocol/recovery explanation, точечный cleanup evidence, storage/hygiene report, Git/legacy status и residual risks. Не объявляй P0 принятым: остановись для повторной приёмки Codex/пользователем.
