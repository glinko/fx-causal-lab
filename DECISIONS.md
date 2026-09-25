# Принятые решения

Дата: 2026-09-23. Основание: ответы пользователя после исходной спецификации.

## Объём и среда

- Самостоятельный исследовательский проект; сначала небольшой сквозной MVP, 5–10 факторов.
- Ubuntu-first, сервер `websrv`, `192.168.88.5`, каталог `/opt/fx-causal-lab`.
- Python 3.12, Docker Compose, Parquet и DuckDB. PostgreSQL и OpenSPG отложены.
- Веб-интерфейс нужен сразу: отчёты, данные, визуализации. CLI сохраняется.
- Исходное окно 2023-09-23–2026-09-23; после отладки расширение до 8–10+ лет.
- Основные горизонты: 1, 5, 20, 60 торговых дней. H1 — дополнительный уровень.
- Публичные источники первыми. Платный consensus опционален; отсутствующие значения остаются null.

## Развёртывание

- Отдельный контейнер на `http://192.168.88.5:8088`, без изменения существующего Caddyfile.
- Это локальный сервис без пользовательской аутентификации. Публичный домен, проброс портов и внешняя публикация не настраиваются.
- Контейнер работает без root, с read-only filesystem; данные на отдельном bind mount.
- Лимит сервиса: 1 CPU и 1 GiB RAM. Сервер одновременно обслуживает существующие сайты.
- KVM guest CPU не предоставляет AVX/AVX2 и ряд SSE-инструкций. Даже совместимая сборка Polars предупреждает о недостающих инструкциях; поэтому первый data slice реализован на стандартном Python и DuckDB, без Polars. Менять CPU VM для запуска не требуется. Версии пакетов закреплены в requirements.lock, base image — по digest.
- Сервис автоматически перезапускается после перезагрузки. Автозапуск загрузчиков пока не включён.

## Данные и время

- `event_time`, `published_at`, `available_at`, `ingested_at` разделены.
- Строгий отбор допускает только `exact_timestamp`. Дневные и консервативные оценки — только в отдельно обозначенном нестрогом анализе; `unknown` исключается всегда.
- Текущая историческая выгрузка ECB хранится как reference rate, без OHLC. Её нельзя выдавать за торговые свечи или за подтверждённые исторические vintages.
- Базовый ECB snapshot имеет unknown historical availability и исключён из строгих моделей. Обычное время публикации не подменяет доказательство времени конкретной исторической записи.
- Исходные ответы сохраняются по hash; каждая загрузка получает отдельную квитанцию с временем, URL и разрешёнными HTTP-заголовками.
- Идемпотентность нормализации определяется одинаковым содержимым и normalized hash; повторные HTTP-загрузки остаются отдельными фактами ingestion.
- Существующая локальная минутная история HistData/Dukascopy не импортируется: её README фиксирует неразрешённые временные сдвиги, дубли и нарушения OHLC.

## Эксперименты

- Pre-event, post-release и reaction-confirmed — отдельные эксперименты.
- Target начинается не ранее prediction_time; окно измерения реакции должно завершиться до prediction_time.
- При наличии consensus обязательно хранить forecast vintage. Surprise без consensus запрещён.
- Первый шаг M5 — попытка репликации опубликованных macro surprise → FX эффектов с документированием выборки, определения surprise, горизонта и ограничений.
- Невоспроизведение требует проверки данных, выравнивания, статистической мощности и сопоставимости периодов. Оно не доказывает само по себе поломку данных или отсутствие эффекта.
- Нулевой результат допустим. Ни граф, ни correlation/feature importance не доказывают причинность.
- Онтология YAML/JSON и NetworkX предшествуют OpenSPG. Автоматические торговые операции не входят в MVP.

## Состояние этой поставки

Развёртывание и первый проверяемый data slice. Это не завершённые M0–M2: полная проверка глубины источников, H1/D1 trading bars, календарь, feature store и статистические эксперименты ещё впереди.

## 2026-09-23 — H1/D1 slice

- Provider: public Dukascopy JSON API, Bid only, immutable raw snapshots and pinned offline manifest replay. Decoder independently follows documented upstream delta encoding (dukascopy-node source).
- H1 remains UTC. D1 aggregates NY17 sessions with DST and provider holiday metadata. No flat candle insertion. Invalid OHLC quarantined; incomplete daily sessions retained with complete=false and excluded from plotted line.
- available_at=bar_end+60s is an explicit inferred assumption. Current historical prices and current calendar are not verified historical vintages; strict PIT eligibility remains false.
- Eurostat EA20 fixed composition and ECOICOP2 TOTAL. Do not splice EA21 silently.
- Current macro downloads are reconnaissance evidence only. Next M3 work is release calendars and archived first releases; M4 targets cannot use revised values as historical facts.

## 2026-09-24 — BLS release archive

- Windows fetches BLS archive pages because the Ubuntu server IP receives HTTP 403; Ubuntu verifies preserved hashes and normalizes offline.
- The embargo timestamp on each archived page is stored as `published_at`. It does not prove historical receipt, so `available_at` remains null and strict PIT eligibility remains false.
- Headline all-items CPI MoM SA, core CPI MoM SA and NFP change come from archived release pages. The November 2025 CPI release reports two-month changes; they use separate `2M` indicators and never enter monthly series. Consensus, surprise and forecast vintage remain null.
- Missing official releases remain gaps. October 2025 is not synthesized or copied from a later database snapshot.

## 2026-09-24 — CFTC TFF positioning

- Используется официальный Futures Only TFF archive, EUR FX contract code `099741`. Tuesday report date описывает состояние позиций и никогда не считается временем доступности признака.
- Сохранённое расписание CFTC является предварительным и не доказывает фактическую публикацию. Для покрытых им строк `available_at` консервативно установлен на 00:00 America/New_York следующего календарного дня; `time_quality=inferred_conservative`, strict PIT отключён.
- Для строк вне сохранённого расписания `available_at=null` и `time_quality=unknown`; они исключаются из as-of выборок. Необычная официальная report date сохраняется без исправления.
- Исходные годовые ZIP и snapshot расписания закреплены hash. Futures + Options и roll adjustment не смешиваются с текущим Futures Only набором.

## 2026-09-24 — FOMC statements

- Источник событий — HTML statements, ссылки на которые взяты из официального FOMC calendar. Special notation votes и strategy statements не смешиваются с решениями по target range.
- `published_at` извлекается из строки `For release at` и проверяется по часовому поясу America/New_York. Это точное официальное время релиза, но не доказательство исторического получения нашей системой; `available_at=null`, strict PIT выключен.
- Target lower/upper извлекаются из текста конкретного заявления; midpoint и изменение в basis points являются детерминированными производными. Первое изменение в запрошенном окне остаётся null.
- Consensus, forecast vintage и policy surprise не восстанавливаются из фактического решения и остаются null.

## 2026-09-24 — ECB monetary policy decisions

- Индекс событий берётся из официальной FOEDB: versions descriptor, metadata и необходимые data chunks сохраняются в Bronze вместе с каждой страницей `Monetary policy decisions`.
- `published_at` берётся из FOEDB `pub_timestamp` и проверяется как 14:15 Europe/Berlin на дату URL. Это официальное время публикации, а не historical receipt; `available_at=null`, strict PIT выключен.
- Deposit facility, main refinancing operations и marginal lending facility извлекаются из текста каждого релиза. Изменение deposit rate — детерминированная производная; для первой строки в окне она null.
- Consensus, forecast vintage и surprise остаются null; фактическое решение их не заменяет.

## 2026-09-24 — M4 event alignment

- `pre_event` использует официальную границу релиза как `prediction_time`; Actual хранится только как event-study metadata и запрещён как feature.
- `post_release` создаётся только если `available_at` известен. `reaction_confirmed` использует `prediction_time = available_at + 1h`; окно реакции завершается не позже prediction time.
- Target начинается с open первой H1-свечи, начавшейся не раньше `prediction_time`. Endpoints — close 1-й, 5-й, 20-й и 60-й NY17-сесии; доходность `close/open - 1` и log-return.
- Неполная D1-сессия инвалидирует свой и все более дальние горизонты; её нельзя пропустить и сжать trading time.
- CFTC pre-event привязан к сохранённому schedule, а post/reaction — к консервативному `available_at`; все эти строки нестрогие. Из-за неподтверждённого market vintage строгих строк в M4 пока 0.

## 2026-09-24 — M5 descriptive baseline

- Published-effect replication начинается с requirements check. Пять первичных исследований занесены в registry; все помечены unavailable, потому что нет historical consensus, futures/OIS surprise factors или данных нужной intraday-частоты.
- Фактический CPI/NFP или изменение policy rate не заменяют surprise. Их знак не используется как pre-event feature.
- Доступный baseline оценивает только безусловную среднюю EUR/USD return после event boundary. CFTC schedule events исключены. Для `n < 8` inference не считается.
- Standard errors — Newey–West/HAC с заранее заданными lag 0/0/1/3 для 1/5/20/60d. Показано число overlapping windows; p-values получают Benjamini–Hochberg q-value по всем diagnostic tests.
- Ни один test не имеет q < 0.05. Это не доказывает отсутствие эффекта и не является репликацией: в текущем baseline нет surprise-признака.

## 2026-09-24 — M6 causal hypothesis graph

- `config/graph_schema.yaml` задаёт разрешённые node/edge types и обязательные поля; `config/causal_graph.yaml` хранит 7 приоритетных исследовательских цепочек. OpenSPG пока не требуется.
- Направленный граф обязан быть DAG. Валидатор отклоняет неизвестные типы и ссылки, дубликаты ID, self-edges, отрицательные или перевёрнутые lag intervals и циклы.
- Каждая связь имеет знак, допустимый lag и `evidence_status`. Стрелка — гипотеза или преобразование, а не автоматически подтверждённая причинность.
- `available_non_strict` означает только наличие текущего набора данных. Это не strict PIT readiness. Ни одна цепочка пока не имеет strict-ready статуса.
- Временные ряды и observation-level события остаются в Parquet. Граф хранит смысловые сущности, связи, ограничения и ссылки на data manifests.
- Portable outputs — JSON и GraphML. Интерактивная веб-карта использует те же экспортированные данные, поэтому визуализация не является отдельным источником истины.

## 2026-09-24 — M7 descriptive interactions

- Без historical consensus нельзя запускать `surprise × positioning` или `surprise × market regime`. Actual и его знак не используются как замена surprise.
- Выполняются два смежных описательных вопроса: event occurrence × CFTC positioning regime и event occurrence × trailing 20-session EUR/USD trend. В отчёте прямо указано, какие исходные гипотезы они не заменяют.
- Positioning feature — последний `leveraged_funds_net_share_oi`, доступный не позже `prediction_time`. Режим определяется expanding percentile только по 8–52 уже доступным отчётам; будущая выборка не участвует в границах.
- FX trend — знак return за 20 полных NY17-сессий. Используются только дневные бары с `available_at <= prediction_time`; target начинается после prediction boundary.
- CFTC availability и market-history vintage остаются non-strict. Поэтому p/q-values, formal regime contrasts, причинный вывод и торговое правило не рассчитываются.
- Planned registry отдельно сохраняет blocked interactions для surprise, oil/inflation expectations и rate differential/risk sentiment. Недоступные входы не синтезируются.

## 2026-09-24 — post-M7 decision gate

- M0–M7 доказали сквозную техническую цепочку, но не строгую исследовательскую готовность: strict PIT rows, доступные published-effect replications и strict-ready graph chains остаются равны нулю.
- OpenSPG и GNN/ML отложены. Они не создадут отсутствующие forecast vintages, policy-surprise factors, yields или исторические availability timestamps.
- Текущий веб-интерфейс достаточен для следующего этапа: он показывает отчёты, граф, ограничения и downloads. Расширение UI не опережает получение данных.
- Этот прежний приоритет отменён решением 2026-09-25 ниже. Readiness review остаётся историческим снимком состояния v0.11.
- Research-unlock score — прозрачная метрика покрытия существующего графа, published replications и planned interactions. Она не является cost-benefit оценкой; стоимость, лицензирование и качество источника пока неизвестны.

## 2026-09-24 — consensus and narrow-window FX acquisition audit

- Dukascopy daily tick files выбраны для бесплатного технического пилота узких окон. Контрольный день 2024-01-11 дал 126811 валидных bid/ask ticks, 1437 M1 bars и 61 M1 bar в окне ±30 минут вокруг 13:30 UTC.
- Tick history остаётся non-strict: доступный сейчас исторический endpoint не доказывает неизменность прошлой версии рынка или право перераспределения. Он разблокирует инженерный пилот, но не строгий causal result.
- Econoday и Trading Economics проходят следующий одинаковый sample test для CPI/NFP. Econoday публично заявляет historical as-released archive с 2001 года; Trading Economics документирует PIT calendar schema и REST API. Эти claims ещё не являются принятым dataset.
- LSEG/Reuters Polls и Bloomberg оставлены enterprise alternatives. Они документируют богатое consensus/release-time покрытие, но не соответствуют minimal-budget bootstrap без существующей лицензии.
- До закупки обязательны: глубина CPI/NFP, финальный pre-release forecast vintage, stable event IDs, first-release Actual, revisions, минимум 95% numeric consensus после объяснённых исключений, права локального хранения и проверка sample против BLS.

## 2026-09-25 — free/public data first и DENN

- Consensus procurement приостановлен. Consensus — optional enrichment; отсутствие vendor sample не блокирует dataset build, DENN feature generation или spectral MVP.
- Existing CPI/NFP tick/M1 работа сохраняется как первый event-study prototype. Она не является главным acquisition workflow.
- Источник считается интегрированным только после фактической загрузки raw payload, нормализации в Parquet, проверки дат/дубликатов/finite values и публикации coverage manifest.
- Tier A: EUR/USD D1/H1, US и euro-area 2Y/10Y, производные spreads, Brent, WTI, VIX и US/EU equity proxies. Общая D1 сетка строится inner join без forward fill.
- Current-history series без доказанных vintages получают `strict_pit_eligible=false`. Это допускает exploratory spectral baseline, но не строгие causal claims.
- Первая DENN реализация: единая snapshot schema, deterministic features, decay kernels и baseline на 5–6 continuous factors. Dynamic GNN, OpenSPG и AnyJev не опережают воспроизводимый walk-forward baseline.
- Главный acquisition report переименован в Open Data Coverage. Vendor материалы остаются в optional/history разделе и не формируют статус готовности MVP.
