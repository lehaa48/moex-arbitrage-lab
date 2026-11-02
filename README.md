# MOEX Arbitrage Lab

Исследовательский проект для проверки направленной гипотезы **COMEX/CME Gold Futures → MOEX GOLD** на сырых сделках. Проект не утверждает наличие лага и не выполняет торговых операций.

Ближайший результат — воспроизводимый one-day vertical slice: два неизменяемых raw-файла, manifests/checksums, нормализованные Parquet, диагностика timestamps и торгового пересечения, таблица и график lead-lag.

## Текущая стадия

Stage A / planning only. Репозиторий содержит архитектурные решения и задания. Importers и исследовательский pipeline ещё не реализованы и не должны запускаться до отдельного подтверждения пользователя.

## Неподвижные ограничения

- фактический бюджет: 0 RUB / 0 USD;
- никаких заявок, сделок, активации торговли или брокерских операций;
- никаких секретов в Git, логах и отчётах;
- никаких рыночных данных вне корня этого проекта или на диске `C:`;
- raw-файлы append-only/immutable, UTC и раздельные event/receive/ingest timestamps;
- trades-only результат не является доказательством исполнимой арбитражной прибыли.

Начать чтение: [charter](docs/PROJECT_CHARTER_RU.md), [technical specification](docs/TECHNICAL_SPECIFICATION_V0_1_RU.md), [status](docs/PROJECT_STATUS.md), [P0](docs/tasks/P0_BOOTSTRAP_AND_MIGRATION.md).

