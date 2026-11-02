# Copy-paste prompt for Claude Code — P0 final bounded recovery correction

Рабочая папка: `D:\project\moex-arbitrage-lab`.

Codex выполнил вторую независимую приёмку P0 corrective pass. Общий offline gate зелёный, но два узких сценария ownership/recovery воспроизводимо нарушают заявленные гарантии. Пользователь согласовал **ещё один строго ограниченный corrective pass сейчас**, чтобы затем перейти к P1. Исправь только перечисленные ниже дефекты, удали один точно идентифицированный cache-файл, повтори evidence и остановись. Не начинай P1/P2, не вызывай provider API, не загружай market data и не перестраивай storage subsystem.

## Перед изменениями

1. Полностью прочитай `AGENTS.md`, `CLAUDE.md`, `docs/tasks/P0_BOOTSTRAP_AND_MIGRATION.md`, `docs/tasks/CLAUDE_P0_REVIEW_FIX_PROMPT.md`, `docs/TECHNICAL_SPECIFICATION_V0_1_RU.md`, ADR-0001…0004, `docs/STORAGE_AND_SECRETS_POLICY.md`, `docs/COST_AND_PURCHASE_POLICY.md` и этот prompt.
2. Зафиксируй новый repo status и legacy repo status/HEAD. Не clean/reset/commit/push.
3. Не меняй H-0001, holdout ledger, source matrix, P1–P4, contracts, dependency files или legacy repository.
4. Не запускай `uv` до guarded environment. На Windows используй `powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned` и `scripts/project_env.ps1`; все cache/temp/pytest/coverage outputs должны оставаться внутри проекта.

## Результат независимой проверки

Предыдущий corrective pass действительно исправил исходные timestamp/manifest findings и удалил три исходных cache-файла. Targeted suite дал `174 passed`; guarded full gate дал `364 passed, 1 skipped`, coverage `98.41%`. Эти результаты не покрывают два сценария ниже.

### Finding 1 — exception rollback удаляет подменённый чужой final path

В `src/moex_arbitrage_lab/settings/raw_publication.py` успешные hard links сохраняются как пути в `linked`, а exception rollback затем без повторной проверки ownership делает `_remove(target)` для каждого существующего пути. Между link и rollback другой локальный writer может удалить hard link этого publisher и создать независимый файл по тому же final path. Rollback удаляет уже чужой файл.

Независимое воспроизведение Codex:

1. дождаться checkpoint `linked:payload`;
2. снять read-only только с этого linked name, unlink его и записать независимый foreign file по тому же path;
3. выбросить исключение;
4. результат текущего кода: `foreign_exists=False`, `foreign_unchanged=False`.

Это нарушает corrective acceptance: concurrent/pre-existing target должен оставаться неизменным. Существующий race-test подставляет target **до** `os.link`; он не покрывает replacement **после** успешного link.

Исправь минимально:

- после каждого успешного hard link сохрани ожидаемую file identity (`st_dev`, ненулевой `st_ino`/Windows file index) независимо от дальнейшего существования staging path;
- rollback удаляет final name только если его текущая identity всё ещё совпадает с сохранённой identity этого publisher;
- marker проверяется/удаляется первым, но только если он всё ещё собственный;
- foreign replacement никогда не `chmod`, не удаляется и не перезаписывается, даже если bytes совпадают;
- ownership нельзя доказывать только path, bytes, hash или `publication_id`;
- cleanup собственных staging/journal остаётся bounded;
- post-commit verification failure удаляет собственный marker/артефакты, но сохраняет любой независимо подменённый foreign target.

Не строй новый transaction engine и не переноси legacy replay/streaming graph. Нужна только ownership-safe версия текущего protocol.

### Finding 2 — recovery принимает foreign marker по одному `publication_id`

Текущий `_is_own_commit` сначала проверяет hard-link identity marker, но при несовпадении/отсутствии staging marker принимает любой schema-valid final marker с тем же `publication_id`. `_settle` после этого сообщает `completed` без полной `verify_published`.

Два независимых воспроизведения Codex:

1. Schema-valid foreign marker с тем же `publication_id`, но отсутствующими manifest/checksum: recovery принимает commit и падает на `chmod` с `FileNotFoundError`.
2. Все final names существуют, foreign marker имеет тот же ID, но неверные hashes/manifest: recovery возвращает `completed=['dddd...']`, затем `verify_published` возвращает `RawIntegrityError`.

Исправь минимально:

- если staging marker существует, final marker считается собственным только при совпадении file identity; другой marker остаётся foreign независимо от совпавшего `publication_id`;
- fallback для нормального crash-окна после удаления staging marker допустим только если marker schema/ID/path корректны **и вся публикация успешно проходит `verify_published`**;
- `RecoveryReport.completed` пополняется только после полной reader-level verification payload + manifest + checksum + marker;
- missing/corrupt/foreign marker или artifacts не должны приводить к `FileNotFoundError` и не должны попадать в `completed`;
- foreign paths не удаляются и не chmod; собственные finals можно удалить только при доказанной file identity;
- если из-за foreign/conflicting path безопасно завершить или полностью откатить нельзя, отрази это явно в минимальном `conflicts`/`unresolved` outcome (или эквивалентном типизированном результате), оставь foreign path нетронутым и fail closed для автоматического продолжения;
- нормальный сценарий `commit marker valid + staging уже удалён + journal cleanup не завершён` должен по-прежнему завершаться как `completed` после полной проверки.

## Обязательные synthetic tests

Добавь тесты как минимум для следующих сценариев. Все fixtures только внутри project-local pytest temp; никакого real raw/holdout/provider access.

1. После успешного link каждого из четырёх targets (`payload`, `manifest`, `checksum`, `marker`) final name заменяется независимым foreign file, затем возникает exception: foreign identity/bytes сохраняются.
2. Post-commit reader verification failure при independently replaced target: собственный marker снимается первым, собственные файлы очищаются, foreign file остаётся.
3. Foreign schema-valid marker с тем же `publication_id`, пока staging marker существует: не `completed`, foreign marker не меняется, recovery не падает.
4. Staging marker уже отсутствует, но вся committed publication валидна и остался journal: полная verification проходит, recovery возвращает `completed` и дочищает journal.
5. Staging marker отсутствует, marker имеет совпавший ID, но artifact отсутствует или hashes/manifest повреждены: recovery не падает, не возвращает `completed`, отражает conflict/unresolved и не удаляет недоказанно чужие paths.
6. Same bytes/different identity остаётся foreign и не удаляется.
7. Все существующие abrupt-termination, live-lock, cleanup-pending, collision и verification tests продолжают проходить.

Тесты должны воспроизводить оба Codex findings на старой реализации и становиться зелёными только после исправления. Не ослабляй существующие assertions/static hygiene checks.

## Точечный cleanup созданного Codex cache-файла

Во время независимой targeted-проверки Codex один раз запустил `uv` после отказа dot-source wrapper из-за execution policy. Этот запуск создал ровно один новый файл:

```text
C:\Users\Aleksey\AppData\Local\uv\cache\interpreter-v4\79f9c1a3c75bd579\92d9c6e269209859.msgpack
```

Зафиксированные свойства: 1630 bytes; creation/last-write `2026-11-02T15:52:16.2933019+03:00`; обычный file; attributes `Archive`; не link/reparse point; `fsutil hardlink list` показал один literal path. До ошибочного запуска в каталоге было пять соседних файлов; после него стало шесть. Пользователь этим corrective pass разрешил удалить **только этот exact file**.

Перед удалением заново проверь literal full path, parent, regular-file type, размер, timestamps, отсутствие reparse/link и ровно один hardlink. Сними snapshot пяти соседних filenames/sizes/timestamps. Если хоть одно свойство не совпадает — ничего на `C:` не удаляй и верни blocker. Если всё совпадает, удали только этот file через literal-path operation; не удаляй каталог и не меняй соседние пять файлов. После удаления докажи:

- exact path отсутствует;
- пять соседних files и их metadata не изменились;
- три ранее удалённых exact paths (`52e99d57eb652d4d.msgpack`, `55aed9fbc3fc0dea.msgpack`, `97d7aae16c3003c7.msgpack`) по-прежнему отсутствуют;
- последующие команды не создали новых out-of-root files.

## Разрешённые изменения

Изменяй только:

- `src/moex_arbitrage_lab/settings/raw_publication.py`;
- `tests/unit/test_raw_publication.py`;
- точные evidence/checksum/status cells в `docs/MIGRATION_MANIFEST.md` и `docs/PROJECT_STATUS.md`.

Не меняй `types.py`, `trade_v1.py`, `acquisition_manifest.py`, `pyproject.toml`, `uv.lock`, scripts, VS Code tasks, H-0001, holdout, source matrix, P1/P2 files или legacy repo. Если минимальное исправление требует выхода за этот список — остановись и верни blocker до изменения.

## Проверка

1. Сначала запусти новые reproduction tests отдельно.
2. Затем весь `tests/unit/test_raw_publication.py` и связанные repository-hygiene/bootstrap tests под guarded environment.
3. Проверь format/lint/types для изменённых файлов до full gate, чтобы не тратить разрешённый полный прогон на статическую мелочь.
4. После targeted green запусти полный offline P0 quality gate **ровно один раз** через штатный guarded wrapper.
5. Запусти guarded storage report и repository hygiene scan.
6. Проверь, что `data/`, `artifacts/`, `reports/`, `.env`, credentials, provider SDK/network entrypoints и `tmp/raw-publication` отсутствуют после тестов.
7. Проверь новые target SHA-256 и обнови только соответствующие evidence cells.
8. Подтверди legacy HEAD `068bbd8d86a935686d741ea0ef1949617f9f0cab` и прежние legacy modifications без изменений.

Если любой test требует network/provider/token, может создать charge, модифицировать real raw/holdout или записать проектное состояние вне `D:\project\moex-arbitrage-lab`, не запускай его и верни `BLOCKED`.

## Финальный ответ

Верни:

- findings → exact fixes mapping;
- изменённые файлы и новые SHA-256;
- краткое описание ownership invariant и recovery state outcomes;
- названия новых reproduction tests и доказательство, что они ловят прежние дефекты;
- все команды, exit codes, targeted/full results и log paths;
- exact cache cleanup evidence до/после;
- storage/hygiene, new-repo и legacy status;
- остаточные риски без завышения гарантий.

Не объявляй P0 принятым самостоятельно, не коммить и не начинай P1/P2. Остановись для финальной приёмки Codex/пользователем.
