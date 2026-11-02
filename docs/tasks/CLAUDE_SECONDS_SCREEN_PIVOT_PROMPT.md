# Промпт для Claude Code — двухмесячный секундный screen H-0001

Рабочая папка: `D:\project\moex-arbitrage-lab`.

Пользователь изменил исследовательский приоритет **до скачивания trade payload и до просмотра lead-lag результатов**. Не делай отдельные one-day/five-day исследовательские итерации. Выполни один упрощённый end-to-end screen на двух завершённых месяцах: **2026-09-01 00:00:00 UTC — 2026-11-01 00:00:00 UTC**, фиксированные контракты `GDZ6_SPBFUT` и `GCZ6`, разрешение одна секунда.

Однодневный canary допустим только как автоматическая проверка формата внутри того же запуска. Это не отдельный этап, не отдельный анализ и не точка ожидания решения пользователя. Все изменения, тесты, commits и pushes выполняет Claude; Codex остаётся независимым reviewer.

## Цель и предел вывода

Цель — быстро ответить на один вопрос: существует ли воспроизводимый секундный predictive lag COMEX → MOEX, достаточно заметный, чтобы оправдать более глубокое исследование.

Скачай MOEX/T-Bank и COMEX/Databento в **разные immutable raw files**, независимо построй секундные ряды, затем выполни заранее зафиксированное сравнение.

Это trades/bar-based screening, а не проверка исполнимого арбитража. Не используй слова `profit`, `executable arbitrage` или `EVIDENCE_OF_LEAD_LAG`. Разрешённые финальные категории только:

- `SCREEN_POSITIVE` — есть повод проектировать следующий, более точный этап;
- `NO_SCREEN_SIGNAL` — на секундном уровне устойчивого сигнала нет, проект останавливается до нового решения пользователя;
- `DATA_INSUFFICIENT`;
- `TIMESTAMP_SEMANTICS_INSUFFICIENT`.

## Сначала перепроверь и сохрани текущее состояние

- `main` и `origin/main` должны указывать на accepted P0 commit `9a28c34` или на его опубликованного потомка.
- В рабочем дереве уже есть незакоммиченный P1 adapter/config/tests. Не делай `reset`, `clean`, `checkout` и не переписывай полезную работу.
- Последний synthetic run до pivot: `362 passed`.
- Уже был один provider request: `HEAD` для `2026-10-06`, результат `200`, файл `GDZ6_SPBFUT_2026-10-06.csv.gz`. Ledger: `logs/tbank/requests.jsonl`. Не повторяй этот HEAD и не утверждай, что provider calls не было.
- Trade payload ещё не был скачан; `data/`/`artifacts/` могли отсутствовать.
- Старый one-day prompt и прежние P1–P4 документы являются историей решения. Обновляй статусы/ADR append-only или помечай прежний маршрут `superseded/deferred`; не стирай историю.

Зафиксируй исходный `git status`, SHA, hashes незакоммиченных файлов и request ledger до дальнейшей работы.

## Multi-agent execution

Загрузи `CLAUDE.md`, `.claude/skills/parallel-stage-execution/SKILL.md`, `.claude/skills/p1-moex-zero-cost/SKILL.md` и применимые agent profiles. Используй 3–4 непересекающихся workstream:

1. main agent — интеграция, provider calls, raw publication, docs/status, commits/pushes;
2. MOEX owner — завершение T-Bank adapter, calendar/session QA и двухмесячное acquisition;
3. seconds/research owner — causal 1s construction, synthetic tests и frozen analysis;
4. independent read-only reviewer — task boundary, leakage, timestamps, cost/storage и evidence.

Один файл имеет одного owner. Не запускай provider calls параллельно. Не создавай новые skills/profiles, если существующих достаточно.

## До первого GET: оформить v0.2 и заморозить protocol

До получения market payload:

1. Создай `docs/experiments/H-0001_SECONDS_SCREEN_V0_2.md`.
2. Обнови `PROJECT_STATUS`, roadmap, active task docs, source matrix, cost policy references и holdout ledger.
3. Явно запиши, что H-0001 v0.1 и строгий sub-second/full research plan не удалены, а `deferred`; v0.2 supersedes прежнюю последовательность one-day → five-day → month для текущего screening decision.
4. Не открывай и не вычисляй outcome до фиксации protocol/config/code.

### Frozen universe

- период: `[2026-09-01T00:00:00Z, 2026-11-01T00:00:00Z)`;
- MOEX: `GDZ6_SPBFUT`, canonical `MOEX:GD:2026-12-18`;
- COMEX: raw symbol `GCZ6`, dataset `GLBX.MDP3`, schema `ohlcv-1s`, `stype_in=raw_symbol`;
- один и тот же декабрьский контракт в оба месяца; никаких continuous symbols и автоматического roll;
- кандидатные дни: все календарные даты периода;
- primary day universe: только предварительно верифицированные общие regular Mon–Fri sessions;
- weekend MOEX sessions, exchange holidays, early close и другие exceptional sessions исключить из primary **по календарю до outcome**, но перечислить и отчитаться отдельно;
- отсутствие/неполнота файла не позволяет заменить день другим; это coverage evidence и заранее заданная причина exclusion/data insufficiency.

Используй официальные календари и actual session coverage. Не hard-code общий интервал без date-specific проверки. Ни один return не пересекает auction, break, закрытие дня или provider gap.

### Discovery/confirmation без повторных загрузок

- September 2026 — discovery/training month;
- October 2026 — locked confirmation month;
- raw обоих месяцев можно приобрести одним проходом, но October outcome нельзя вычислять/просматривать до freeze discovery implementation/config;
- format/coverage/QA October не считается outcome, однако не используй его для настройки лагов, stale caps, thresholds или exclusions;
- после September analysis зафиксируй code commit + config hash + выбранный `h*`, append holdout ledger, затем открой October outcome ровно один раз;
- не нужна остановка для user review между September и October, если protocol не менялся и все safety gates зелёные;
- если после просмотра October требуется изменить метод, October становится training evidence; текущий result не переименовывать в confirmation.

## MOEX/T-Bank acquisition — два месяца, без лишних probes

Сохрани текущий P1 adapter/tests и исправь известные дефекты до GET:

1. observed payload — gzip CSV (`.csv.gz`), не ZIP;
2. opening auction 06:50–07:00 MSK отделён от continuous sessions; применяй официальные date-specific часы;
3. October weekend sessions существовали, а 24–25 October были отдельно отменены; не пиши «в октябре исключений не было»;
4. archive request identifier — `GDZ6_SPBFUT`; не заявляй глобальную уникальность ticker+class;
5. каждый target row обязан иметь `TICKER_CC == GDZ6_SPBFUT`; во всём target set должен быть ровно один валидный UID, статус `OBSERVED_FROM_ARCHIVE_NOT_INDEPENDENTLY_VERIFIED`;
6. raw timestamp precision сохраняется, даже если screen секундный.

Составь deterministic request plan на кандидатные даты, исключив заранее календарные weekend/known-closed dates. Не делай HEAD для каждого дня. Canary — первый запланированный GET, предпочтительно ordinary weekday `2026-09-02`; он входит в итоговый dataset и потому не является лишней загрузкой. После успешной проверки gzip/schema/identifier продолжай тем же кодом остальные дни с лимитом не выше 20 файлов/минуту (ниже provider limit 30/min/IP).

Каждый request записывается в append-only ledger. Payload publish create-once под `data/raw/tbank/`; повторный запуск использует checksum-verified cache и не обращается к provider. На ошибке/коллизии/неожиданном HTML/corrupt gzip остановись, не перезаписывай и не делай бесконтрольный retry.

Для каждого файла и всего периода выдай bytes/SHA-256, request manifest, UID, CSV/raw/eligible counts, exclusions, timestamp quantum/ties/gaps/duplicates, session coverage и source composition. Primary rows: только `TRADE_SOURCE=EXCHANGE`; raw bytes неизменны.

## COMEX/Databento — дешёвые готовые секунды

Не запрашивай `trades`, MBO, MBP или TBBO. Для текущего screen нужен один exact request:

```text
dataset = GLBX.MDP3
schema = ohlcv-1s
stype_in = raw_symbol
symbols = GCZ6
start = 2026-09-01T00:00:00Z
end = 2026-11-01T00:00:00Z
delivery = one batch job
```

Официальная схема `ohlcv-1s` даёт OHLCV только для секунд, в которых была сделка; `ts_event` обозначает начало `[t,t+1s)` и bars построены по `ts_recv` исходных trade messages. Зафиксируй это точно. Такой COMEX ряд показывает время наблюдения данных провайдером, а не чистый matching-engine event-time lag. Это ограничение не исправлять выдуманной точностью и обязательно отразить в result category/limitations.

### Жёсткий cost gate

Сначала Step A: только free metadata/symbology/account calls и `metadata.get_cost` для **точно указанного двухмесячного запроса**. Проверь schema availability, raw symbol definition/expiry, expected bytes, local cache, available credit и возможность hard zero-charge. Token только из `DATABENTO_API_KEY`; не печатай, не сохраняй, не проси присылать в чат.

До Step B остановись и верни пользователю/Codex:

- exact request;
- estimate USD/bytes;
- redacted credit facts;
- `FREE_CONFIRMED` либо `STOP_USER_DECISION`;
- доказательство, что identical payload ещё не закэширован.

Даже если estimate равен нулю или покрывается credit, **нужно отдельное явное approval exact estimate**, потому что ADR-0001 и cost policy уже приняты. Это единственная обязательная внешняя stop point. Non-zero cash charge запрещён.

После approval сделай один batch request. Не делай отдельный платный canary/stream и не повторяй metered request. Скачай batch output, проверь до публикации и сохрани immutable native/raw payload + manifest/checksum. Повторное скачивание только из уже оплаченного batch job, если provider явно гарантирует отсутствие нового data charge, и только при необходимости восстановления.

## Детерминированные секундные ряды

MOEX и COMEX остаются отдельными derived datasets. Минимальная схема:

```text
second_utc,open,high,low,close,volume,trade_count,age_ms,refreshed,session_id,trading_date
```

Для Databento `trade_count` может быть null/unsupported, если `ohlcv-1s` его не предоставляет; не выдумывай. Для каждого ряда:

- seconds — half-open `[t,t+1s)`;
- значение bar close доступно только после конца этой секунды; predictor at decision boundary не может использовать незакрытый bar;
- MOEX строится из raw trades детерминированно;
- пустые секунды не backfill из будущего;
- causal last-known state разрешён только внутри непрерывного session segment;
- primary staleness cap: 5 s; frozen sensitivities: 1 s и 10 s;
- никакого переноса через break/day/session boundary;
- сохрани full raw timestamp precision отдельно;
- manifest содержит raw SHA-256 list, parser/config/code hash, counts/reconciliation и output SHA-256.

Не добавляй тяжёлый dataframe/plotting stack без необходимости. CSV/JSON/Parquet machine-readable outputs обязательны; plot необязателен.

## Frozen seconds analysis

Корреляция price levels запрещена. Используй one-second log returns:

```text
r_t = log(P_t / P_{t-1})
corr(COMEX return at t, MOEX return at t + lag)
positive lag => COMEX leads MOEX
```

Frozen symmetric lag grid, seconds:

```text
-60, -30, -10, -5, -2, -1, 0, +1, +2, +5, +10, +30, +60
```

Для каждого day/lag/staleness report Pearson correlation, paired N, venue refresh/stale fractions и exclusions. Pooled summaries не должны давать большим дням скрытый вес: primary month statistic — equal-day-weighted mean Fisher-z daily correlation с обратным преобразованием. Также сохрани pooled correlation как diagnostic.

### September discovery

- На положительной сетке `{1,2,5,10,30,60}` выбери `h*` как максимальный positive equal-day statistic; deterministic tie-break — меньший lag.
- Если все discovery correlations `<= 0`, всё равно зафиксируй `h*` deterministic maximum и выполни заранее заданный October check; не меняй grid.
- Зафиксируй `h*`, code SHA, config hash и data manifests до October outcome.

### October confirmation and controls

Primary October statistic — equal-day Fisher-z mean correlation только при frozen `h*`. Оцени one-sided uncertainty resampling/permuting whole days или session-preserving 60-second blocks с fixed seed, не менее 2000 deterministic replicates. Secondary simultaneous grid result использует max-statistic 95% threshold для `{1,2,5,10,30,60}`.

Обязательные controls:

- reverse direction на symmetric lag;
- day-mismatch permutation COMEX/MOEX;
- large circular time shift/placebo;
- impossible-time/future-leak control;
- staleness 1/5/10 s;
- per-day sign table и strongest-day share;
- September vs October comparison.

### Data gate

Для primary confirmation требуется:

- минимум 15 eligible regular October days;
- минимум 3600 eligible paired seconds на каждом включённом дне под cap 5 s;
- stale/excluded fraction < 50% для каждого venue/day;
- timestamp semantics/quantum и common-session map опубликованы;
- raw → seconds → aligned reconciliation без unresolved mismatch;
- результат не определяется одним днём: strongest-day contribution < 35% абсолютного aggregate signal.

Не ослабляй gate после просмотра outcome.

### Decision rule

`SCREEN_POSITIVE` только если одновременно:

1. data gate пройден;
2. October correlation at frozen `h*` положительна и выше заранее построенного one-sided 95% null threshold;
3. не менее 60% eligible October days имеют положительную correlation at `h*`;
4. October reverse-direction value at symmetric lag слабее по абсолютной величине;
5. day-mismatch, circular-shift и impossible-time controls не воспроизводят primary effect;
6. знак сохраняется при caps 1/5/10 s хотя бы в 2 из 3, обязательно включая 5 s;
7. нет single-day domination.

Если data gate пройден, но хотя бы одно условие не выполнено — `NO_SCREEN_SIGNAL`. Не подбирай другой lag или threshold после результата. Положительный screen разрешает только предложение следующего этапа с trade-level/top-of-book data; он не доказывает арбитраж.

## Tests и leakage traps

До real analysis обязательны synthetic tests:

- известный +5 s COMEX lead восстанавливается с правильным знаком;
- zero-lag и reverse-lag cases;
- bar close не доступен до конца `[t,t+1s)`;
- strict backward state/no future interpolation;
- session/day/break boundary;
- stale caps 1/5/10;
- timestamp ties/coarse clock;
- day-weighted aggregation;
- September `h*` freeze и запрет настройки по October;
- deterministic block/day placebo;
- missing day/corrupt gzip/cache collision/create-once publication;
- no network in default test gate.

Сначала targeted tests, затем один guarded full offline quality gate после integration. Independent reviewer проверяет executable code и реальные manifests/counts, а не только prose.

## Git, storage и финальный отчёт

- Все project files только под `D:\project\moex-arbitrage-lab`.
- Raw/derived/artifacts/logs/account facts/tokens не коммитить.
- Code/tests/config/docs коммитить и push в `main` без force/amend опубликованного P0.
- Полезную текущую P1 работу сохранить; перечислить её исходные и финальные hashes.
- Можно сделать отдельный freeze commit перед открытием October, затем final evidence/status commit. Raw data остаются ignored.

Финальный ответ должен содержать:

- agent roster/ownership;
- commits и push results;
- exact dates requested/included/excluded с reason codes;
- все provider requests, ledger paths, costs и отсутствие cash charge;
- raw manifests, bytes, SHA-256 и count reconciliation;
- MOEX/COMEX seconds schema/counts/hashes и timestamp semantics;
- September discovery table, frozen `h*`/config/code hash;
- October primary statistic, uncertainty, per-day signs и controls;
- final category из четырёх разрешённых;
- limitations, особенно Databento `ohlcv-1s`/`ts_recv`, trades-only nature и отсутствие execution claim;
- targeted/full test commands, exits и logs;
- residual blockers/risks.

Если Databento key отсутствует, закончи всю offline implementation и безопасную T-Bank часть, commit/push их и верни единственный blocker для Step A. Если estimate получен, остановись перед Step B для exact-estimate approval. Не симулируй реальные результаты и не self-accept stage.
