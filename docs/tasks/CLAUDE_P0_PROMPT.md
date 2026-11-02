# Copy-paste prompt for Claude Code — P0

Рабочая папка: `D:\project\moex-arbitrage-lab`.

Пользователь явно авторизовал только P0 2 ноября 2026 года. Выполни минимальный bootstrap и согласованную миграцию инфраструктуры по `docs/tasks/P0_BOOTSTRAP_AND_MIGRATION.md`, затем остановись и верни evidence bundle. P1, P2, importers и рыночные данные не начинай.

## Перед изменениями

1. Подтверди `Get-Location` и прочитай полностью:
   - `AGENTS.md`;
   - `CLAUDE.md`;
   - `docs/tasks/P0_BOOTSTRAP_AND_MIGRATION.md`;
   - `docs/MIGRATION_MANIFEST.md`;
   - `docs/TECHNICAL_SPECIFICATION_V0_1_RU.md`;
   - `docs/STORAGE_AND_SECRETS_POLICY.md`;
   - `docs/COST_AND_PURCHASE_POLICY.md`;
   - ADR-0001…0004.
2. Зафиксируй исходный `git status --short --branch`. Репозиторий новый, planning-файлы могут быть untracked: это ожидаемое пользовательское состояние. Не удаляй, не reset/clean и не перезаписывай их.
3. Старый репозиторий `D:\project\moex-event-alpha` — строго read-only. Источник истины только commit `068bbd8d86a935686d741ea0ef1949617f9f0cab`; читай через `git show`, `git ls-tree`, `git cat-file`. Не используй legacy working tree и не меняй его.
4. До копирования каждого разрешённого источника проверь Git blob OID и SHA-256 против `docs/MIGRATION_MANIFEST.md`. Переноси идею/минимальный код с адаптацией, а не весь старый subsystem.

## Реализуй только P0

- Python 3.12 project/package `moex_arbitrage_lab`, новый `pyproject.toml` и свежий `uv.lock`; не копируй старый lock и не добавляй T‑Invest/Databento SDK.
- Fail-closed environment/bootstrap и storage-path guard для точного корня `D:\project\moex-arbitrage-lab`. Все `.venv`, cache, temp, logs, artifacts и data — внутри проекта; path traversal, absolute escape, symlink/junction/reparse escape и проектное хранение на `C:` должны отклоняться.
- Research runtime с единственным допустимым trading mode `disabled`; никаких order/trading interfaces.
- Версионированные строгие contracts для acquisition manifest и canonical `trade_v1` из технического ТЗ. `event_ts_utc_ns`, receive/capture timestamps остаются nullable; `local_ingest_ts` никогда их не заменяет.
- Минимальные create-once raw publication + SHA-256/provenance utilities для будущих дневных файлов. Не переноси streaming segment/catalog/checkpoint/replay graph.
- Targeted synthetic/unit/contract tests: root/path escape, reparse/junction, immutable collision, checksum/provenance, missing event timestamp, secret redaction, trading fail-closed, exact schema fields.
- Offline default quality gate. Сетевой test suite по умолчанию отсутствует/не запускается.
- Сделай рабочими VS Code tasks `P0: bootstrap environment`, `P0: verify storage`, `Tests: run relevant tests`. Будущие P1/P2/P3 tasks должны завершаться понятной ошибкой до появления соответствующих entrypoints, а не делать сеть.
- Обнови только evidence/checksum/status части `docs/MIGRATION_MANIFEST.md` и `docs/PROJECT_STATUS.md`.

## Жёсткие запреты

- Не вызывай T‑Bank, MOEX market-data/ISS download endpoints, CME, Databento и любые market-data APIs.
- Не запрашивай и не используй токены. Не создавай `.env` со значениями.
- Не скачивай рыночные данные и не создавай платных обязательств.
- Не реализуй source adapters, Parquet normalization, lead-lag analysis или live recorder.
- Не переноси `collectors/tinvest`, credential manager, network policy, old source registry, streaming storage/replay, news strategy, execution/risk, старые agents/plans/status/data/artifacts.
- Не изменяй experiment H‑0001, holdout ledger, DATA_SOURCE_MATRIX или P1–P4.
- Не делай commit/push и не начинай следующий этап без отдельного указания пользователя.

Доступ к стандартному Python package index разрешён только если он действительно нужен для бесплатного разрешения/установки объявленных P0-зависимостей. Зафиксируй обращение в evidence. Никаких иных сетевых адресов.

## Организация работы

Если используешь subagents, дай им непересекающиеся области файлов. Read-only legacy inspection можно делегировать, но главный агент обязан сам проверить blob/checksum и фактический diff. Несколько агентов не должны редактировать один файл.

Не расширяй платформу «на будущее». При противоречии или необходимости выйти за file ownership остановись и верни blocker, не импровизируй.

## Проверка

Запусти targeted tests изменённой области и один полный offline P0 quality gate после фактической миграции. Проверь:

- все runtime/generated paths находятся внутри workspace;
- нет секретов и непустых token/API-key assignments;
- нет market-data файлов;
- нет импортов/namespace `moex_event_alpha`, T‑Invest/Databento SDK и trading/order capability;
- старый repo сохранил исходный status;
- повторная raw publication по тому же пути fail-closed;
- missing source event timestamp не подменяется ingest timestamp;
- новый lock не содержит legacy broker/gRPC dependency graph.

## Формат финального ответа

1. Outcome: completed или blocked.
2. Список созданных/изменённых файлов.
3. Что именно адаптировано из каждого M‑001…M‑009/M‑019; source blob, source SHA-256, target path и target SHA-256.
4. Точные команды, exit codes и ключевые логи.
5. Результаты targeted tests и полного offline P0 gate.
6. Path-resolution/storage report.
7. Dependency/lock summary.
8. Hygiene scan: secrets, market data, legacy namespace, forbidden modules.
9. `git status --short --branch` и изменение legacy repo status до/после.
10. Отклонения, остаточные риски и blockers.

После этого остановись. Не предлагай считать P0 принятым автоматически: приёмку выполняет Codex/пользователь.
