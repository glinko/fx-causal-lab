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

## 2026-09-25 — DENN deterministic baseline v0.14

- `denn/` сначала проверяет data/temporal contract. Нейросеть и обучаемые графовые веса не добавляются, пока детерминированный pipeline не воспроизводится end-to-end.
- Unified snapshot хранит observation day, source, unit, acquisition timestamp, source snapshot, nullable publication/availability/revision timestamps и явный quality. Current-history inputs остаются non-strict.
- Начальные half-life являются конфигурационными priors по типу ряда. Экспоненциальная память и признаки используют только текущие и прошлые наблюдения; priors пока не оптимизируются.
- Первый benchmark использует шесть continuous features: US–EA 2Y/10Y spread z-scores, 20-session Brent/WTI asinh changes, VIX z-score и EUR/USD momentum. `asinh` для нефти выбран потому, что реальная WTI history содержит отрицательную цену апреля 2020 года.
- Ridge penalty выбирается на предыдущем календарном году. Перед validation/test training rows purged по `target_end_date`; target начинается со следующей common-grid даты. Overlapping horizons не пересекают границу fold.
- ECB EUR/USD reference и current-history inputs не являются executable PIT market state. Поэтому результаты описываются как non-strict OOS diagnostics, а не causal effect или торговая стратегия.
- Dukascopy D1 остаётся отдельным market-price dataset, но его локальное отсутствие больше не блокирует long common grid: обязательный DENN bootstrap использует явно помеченный ECB reference. Отсутствующий optional ряд фиксируется как `MISSING_LOCAL_DATA`, а не подменяется.
- Следующий исследовательский слой — spectral baseline (FFT/wavelet/coherence/lag) на той же закреплённой сетке. Dynamic GNN остаётся после него.

## 2026-09-25 — DENN spectral baseline v0.15

- Spectral preprocessing зафиксирован до анализа: EUR/USD и VIX log differences, yield-spread differences, Brent/WTI asinh differences. Forward fill и интерполяция запрещены.
- Частота определяется в common-grid sessions, а не календарных днях: common grid нерегулярен по календарю из-за разных праздников источников.
- Welch contract: Hann windows длиной 256 sessions, step 128; пять заранее заданных period bands от 2 до 256 sessions. Haar decomposition использует шесть уровней.
- Положительный lag означает `factor[t]` против `EURUSD[t+lag]`; положительный phase-derived lead означает factor leads target. Оба соглашения закреплены synthetic test с известным периодом и задержкой.
- Config identity нормализует CRLF/LF перед SHA-256, чтобы Windows checkout и Ubuntu release не создавали разные dataset IDs из одного YAML.
- Coherence, phase и максимум по 121 лагу — full-sample descriptive diagnostics. Они не являются out-of-sample signal, причинностью или significance test.
- Самая высокая mean coherence в полном sample относится к 2Y spread changes в полосе 30–90 sessions, но phase показывает обратный порядок: EUR/USD leads примерно на 41 session. Этот результат нельзя использовать как подтверждение rates → FX.

## 2026-09-25 — DENN spectral stability v0.16

- Параметры stability зафиксированы отдельно от full-sample config: rolling 1024 sessions с шагом 256; expanding от 1024 sessions с шагом 256; зарегистрированные лаги 0/1/5/20.
- Если шаг не попадает точно в конец sample, добавляется последнее end-aligned окно. Это правило является частью deterministic contract.
- Для каждой factor×window вычисляются те же пять spectral bands. Отчёт показывает modal band share, распределение mean coherence и consistency знака phase-derived lead.
- Lag stability не сканирует максимум: сводка строится только по четырём заранее заданным лагам. Веб-отчёт выделяет lag +1 как проверку результата v0.15.
- Rolling/expanding результаты не являются prediction folds, significance test или причинным доказательством. Dynamic/graph training остаётся заблокирован до интерпретации устойчивости и следующего design gate.
- Rolling-окна перекрываются на 75%, expanding-окна вложены. Доли окон не интерпретируются как независимые вероятности; modal-band comparison также учитывает, что slow band содержит меньше frequency bins и имеет повышенную sampling variability.
- Dynamic GNN остаётся заблокирован до rolling/expanding spectral stability: знак, band, phase и lag должны проверяться на временных окнах без отбора по полному sample.
- Главный acquisition report переименован в Open Data Coverage. Vendor материалы остаются в optional/history разделе и не формируют статус готовности MVP.
- Корректировка от v0.17 (2026-09-25): lag +1 связь 2Y spread не выдерживает строгих publication cutoff'ов — см. следующий раздел.

## 2026-09-25 — DENN timing/cutoff audit v0.17

- Пререгистрированная спецификация в `config/timing_audit.yaml` (version `denn-timing-audit-1`): мир-снапшот модель публикаций, 5 decision-time cutoff'ов (07:00 / 15:00 / 16:30 / 19:30 / 21:00 NY, DST-aware), зарегистрированные лаги 1/2/5/20, Bonferroni 0.05/4 = 0.0125, pair-residual bootstrap (1000, seed 20260925, p только для best registered lag), sign stability по половинам, placebo сдвиг фактора на 20 сессий, exploratory scan ±10 сессий (только для отчёта).
- Модель публикаций (документированные допущения): US Treasury 15:30 ET, EA AAA кривая 18:50 NY (base case), ECB reference 16:00 CET, Brent/WTI EIA +1 день, VIX 15:15 ET. Фактор с grid-датой t доступен для решения на дате D только если его effective publication строго раньше cutoff(D); для каждого решения берётся последний доступный фактор.
- Итог по кандидату v0.16 (`SPREAD_2Y_CHANGE`): naive lag 1 r = −0.198, p = 0.001 (полное воспроизведение v0.16); при строгих cutoff'ах (strict_07, before_treasury_15, after_us_1630) best registered lag = 5, r = −0.031, p = 0.021 > 0.0125 — не проходит. Сигнал появляется только в cutoff'ах после 19:30 (after_ea_1930, end_of_day_21), где доступна вечерняя EA AAA кривая, — точное воспроизведение naive статистики. Вердикт: `timing_artifact`, confirmed = False.
- Интерпретация: «lag +1» v0.16 фактически использует EA кривую, опубликованную вечером того же grid-дня (после завершения NY сессии). В рамках строгой PIT-рамки заявка не воспроизводится; при вечернем окне доступности связь сохраняется.
- Контроли: `SPREAD_10Y_CHANGE` — timing_artifact (и placebo 20 сессий значим, p = 0.002 — дополнительный красный флаг); `VIX_RETURN` — timing_artifact (strict best r = 0.019, p = 0.169); `BRENT_CHANGE` и `WTI_CHANGE` — not_reproducible (best p = 0.042 и 0.016, порог 0.0125 не пройден).
- EA sensitivity (16:30/17:30/18:50/19:30/21:00 NY): strict_07 инвариантен по построению; after_us_1630 не меняется во всех случаях; after_ea_1930 значим только при EA ≤ 19:30. Вывод устойчив к reasonable range допущений о времени публикации EA.
- Найден и исправлен bug bootstrap: первая версия ресемплила residuals вокруг альтернативы (fitted model), что центрирует r* в r_obs и даёт p ≈ 0.5 для любого сигнала. Исправлено на null-модель H0: slope = 0 (y* = ȳ + residual_i); OLS используется только для масштаба residual. Поведение зафиксировано тестом: signal → p < 0.05, независимые данные → p ≈ 0.5.
- Отчёт: `data/reports/denn_timing_audit.json` + `gold/denn_timing_audit/<id>/lag_table.parquet`. `strict_pit_eligible: false` — данные current-history downloads без ALFRED/FRED винтежей; строгие PIT-утверждения не выдаются (inference_status: `multiplicity_controlled_published_at_model_without_vintages`).
- Следующий шаг v0.17: state-vector suite (interactions, leave-one-out ablation, conditional effects), затем DENN memory-features ablation. Wavelet coherence и Dynamic GNN остаются после них.

## 2026-09-25 — Методологическая директива: емерджентное состояние

- **The unit of analysis is not an isolated factor. The unit of analysis is the economic state vector.** EUR/USD считается емерджентным результатом совместного состояния множества факторов: `Y(t+h) = F(Oil_t, Rates_t, VIX_t, Gold_t, Equities_t, Positioning_t, Macro_t, Regime_t, History_t)`. Частные эффекты сами зависят от состояния: `∂Y/∂Oil = g(Rates, VIX, Inflation, Regime, ...)`.
- **Do not use pairwise correlation as a feature-selection gate.** Univariate Pearson/Spearman/coherence/lag-scan — diagnostics ONLY: они отвечают на вопрос «есть ли простой marginal relationship?», а не «фактор полезен/бесполезен».
- Фактор может иметь слабую или нулевую маргинальную корреляцию с EUR/USD и одновременно нести conditional, interaction, mediated, regime-dependent или lagged предиктивную информацию. Пять механизмов: (1) условная зависимость (нефть действует только при определённом уровне инфляции); (2) interaction (β1(Oil)≈0, но β3(Oil×VIX)≠0); (3) mediation (Oil → inflation expectations → rates → EURUSD, многоузловая цепь размывает парную связь); (4) cancellation (два противоположных канала дают сумму ≈0); (5) regime switching (2012 vs 2022: склейка 20 лет усредняет два реальных эффекта до нуля). Канонический пример: `Y = X1 ⊕ X2` — каждая маргинальная корреляция ≈0, совместное состояние определяет Y идеально.
- Фактор НЕ помечается irrelevant по одному только малому standalone correlation.
- Обязательные слои экспериментального стека: 1) univariate diagnostics; 2) multivariate linear baseline (есть: v0.14); 3) interaction terms (preregistered); 4) regime-conditioned effects; 5) nonlinear multivariate baseline; 6) leave-one-factor-out ablation; 7) conditional permutation importance; 8) temporal/lagged interactions; 9) multivariate spectral/wavelet decomposition.
- Отдельно отчитываем: marginal association; conditional predictive contribution; interaction contribution; regime dependence; temporal dependence. Фактор с низкой маргинальной корреляцией, но положительным воспроизводимым OOS ablation value остаётся полезным.
- Synergy — исследовательская ветвь (не gate): conditional mutual information, SHAP interaction values, Friedman H-statistic, Partial Information Decomposition.
- Перечитывание существующих результатов: «Oil has weak coherence» (v0.15–v0.16) означает только «нет сильной стабильной самостоятельной линейно-частотной связи в проверенном представлении данных», а НЕ «нефть не нужна».
- Граница с timing audit v0.17: timing audit — это НЕ pairwise feature selection; это пререгистрированный PIT-тест конкретной маргинальной заявки (ΔSpread lag 1 → EURUSD next day). Её вердикт (timing artifact) не распространяется на полезность фактора в совместном состоянии — именно её тестирует state-vector suite.

## 2026-09-26 — DENN state-vector suite v0.17: результаты

- Реализован и запущен на gold-матрице v0.14 (41 736 snapshots, 5 098 feature rows). Протокол пререгистрирован в `config/state_vector.yaml` до просмотра данных: 6 LOO-ablation + 5 interaction-членов + 1 joint-модель = семья 12, Bonferroni 0.05/12 = 0.00417. Consistency-якорь пройден: baseline state-vector suite воспроизводит aggregate v0.14 до 17 знаков (1d mse = 2.8474753292136997e-05). Отчёт: `data/reports/denn_state_vector.json`, gold `data/gold/denn_state_vector/7e4dd27dcb4bb6f99f9b/`.
- **Главный вердикт: на текущей матрице совместная state-vector модель не даёт значимого OOS-улучшения над multivariate baseline.** Ни один из 5 пререгистрированных interaction-членов не улучшает OOS MSE ни на одном горизонте; joint-модель на 1d ухудшает (dMSE +1.6e-7, 13/17 folds против, p=0.049 — проходит только нескорр. α=0.05, не Bonferroni 0.00417) — добавление терминов переобучает. Это сохранённый null-результат, а не провал: система спроектирована фиксировать его.
- **Единственное directionally-consistent положительное направление: spread_2y_z60.** Ablation (LOO): dMSE > 0 (удаление ухудшает) на всех 4 горизонтах — 1d +1.4e-7 (12/17, p=0.143), 5d +6.2e-7 (9/17), 20d +1.3e-6 (9/17), 60d +3.1e-5 (10/17, p=0.629). Ни один горизонт не проходит Bonferroni; это «неотклонённый кандидат», не сигнал. Направление совпадает с маргинальной диагностикой (oos_r 60d = −0.229, наибольшая в сьюте) и с v0.16 lag-scan.
- **Redundancy finding:** permutation importance (diagnostic) показывает spread_2y_z60 = 0.0% (dMSE ровно 0) на всех 4 горизонтах — в joint-модели ridge обнуляет коэффициент 2Y spread как коллинеарный к 10Y spread; 10Y spread несёт общий rates-канал (permutation 1.8–11.7%, растёт с горизонтом). Ablation и permutation расходятся по определению (ablation = non-redundant contribution, permutation = raw sensitivity), расхождение само по себе — finding о структуре данных.
- **Regime-срез:** ни один из 4 pre-registered режимов (VIX elevated/calm, spread wide/narrow) не показывает положительного skill: все skill_vs_mean отрицательны (1d: −0.84%/−0.56%/−0.21%/−0.23%; 20d elevated_vol −22.5% — худший). Линейная модель не имеет skill ни в одном режиме; это расширяет v0.14 null control до regime-условного уровня.
- **Ни один из 12 вариантов не проходит Bonferroni 0.00417 — ни положительный, ни отрицательный.** Ближайшие near-miss'ы (только нескорр. α=0.05): joint 1d (p=0.049) и oil_x_rate_spread 1d (4/17 в пользу, p=0.049, dMSE +3.4e-8) — оба в направлении ухудшения, т.е. знак против полезности. Значимых положительных результатов нет.
- **Permutation importance (diagnostic-only):** все значения малы (максимум spread_10y_z60 +11.7% на 60d, vix_z60 +6% на 1d/5d); ни один столбец не доминирует. Слой вне Bonferroni-семьи; выводы по полезности — из paired ablation deltas.
- **Следующие шаги:** (1) DENN memory-features ablation (baseline vs baseline+age/decay) — следующий пункт v0.17; (2) nonlinear multivariate baseline (LightGBM/RF) как слой 5 стека — требует sidecar-окружение с numpy/sklearn, контейнер core не имеет их; (3) temporal/lagged interactions (слой 8) — lag'и терминов, а не только level; (4) multivariate wavelet coherence (слой 9).

## 2026-09-26 — v0.18: переход к economic blocks; пререгистрация grouped suite

**Checkpoint:** NULL v0.17 заморожен как исторический чекпоинт — поправки к DECISIONS.md (исправление формулировок о Bonferroni/permutation) и результат `denn_state_vector.json` закоммичены в `eb26ef0` ДО каких-либо новых экспериментов. Секция v0.17 не изменяется; новые гипотезы ниже зафиксированы до просмотра результатов grouped-прогона.

### Директива (user, 2026-09-26)
- Единица анализа поднимается со столбца до **экономического блока**. Сигнал v0.17 (2Y spread: положительный LOO-ablation на всех горизонтах + 0% permutation importance) читается как «2Y и 10Y — две проекции одного латентного фактора: relative rates / policy differential»; single-column importance не видит совместный вклад блока.
- Матрица строится по блокам world-state, а не по индикаторам: внутри блока десяток исходных рядов, модель/PCA получает скрытое состояние блока `H_rates, H_risk, H_flows, ...` вместо дублирующих колонок.

### Пререгистрированный world-state (Tier A — открытые источники; Tier B — платные/ограниченные)
| Блок | Состав (Tier A) | Статус |
|---|---|---|
| rates / monetary | US-EA 2Y/5Y/10Y дифференциалы, 2s10s (US, EA), relative curve slope | есть (6 cols → блок rates) |
| inflation expectations | Fed TIPS real 5Y/10Y + inflation compensation 5Y/10Y (ежедневно, с 1999); ECB Consumer Expectations Survey (5y exp., monthly) | TIER A, к загрузке |
| growth | (позже; на горизонте месяцев) | backlog |
| risk / financial conditions | VIX + Chicago Fed NFCI/ANFCI и подкомпоненты risk/credit/leverage (weekly, с 1971) | TIER A, к загрузке |
| funding / liquidity | открытые прокси: SOFR/repo, bank funding stress, Fed liquidity (H.4.1 weekly); cross-currency EURUSD basis — TIER B (дорогой) | TIER A, к загрузке |
| energy / ToT | Brent, WTI + European-specific: TTF gas, US nat gas (проверить лицензию/глубину), European electricity proxy → relative shock `EnergyShock_EA − EnergyShock_US` | TIER A, к загрузке |
| capital flows | Treasury TIC (monthly): NetForeignDemand_US, portfolio flows; interaction `Flows × RateDifferential` | TIER A, к загрузке |
| positioning | CFTC (уже есть) | есть |
| relative equity | `R_US equities − R_EU equities` (не S&P сам по себе); позже: US Banks vs EU Banks, US Tech vs EU Industrials | TIER A, к загрузке |
| CB liquidity | Fed H.4.1 + ECB balance sheet (SDMX): growth-normalized, relative liquidity impulse (slow-state variable) | TIER A, к загрузке |
| FX state | momentum (есть) + **USD-ex-EUR factor** (USDJPY, GBPUSD, USDCAD, USDCHF, ...) — не DXY (тавтология по EUR); вопрос: движение USD вообще vs специфическое EUR | TIER A, к загрузке |
| trade / ToT | US/EA trade balance, current account, relative terms of trade (monthly) | backlog |
| gold | уже в планах | backlog |

### Пререгистрированный экспериментный стек (все — purged walk-forward, frozen hypotheses, multiplicity control, null preservation)
1. linear baseline (есть, v0.14); 2. ElasticNet; 3. **group ablation** (этот прогон — первый на текущей матрице); 4. group permutation; 5. PCA per block; 6. LightGBM (sidecar-окружение); 7. + decay memory; 8. + spectral features.
- Orthogonalized shocks (oil/equity/gold residuals после общих financial factors) — пререгистрированный приём для блоков energy/equity после их расширения.
- Если LightGBM на замороженной матрице тоже не найдёт signal — существенно сильнее: в текущих данных мало predictive information. Если nonlinear стабильно выигрывает — первая поддержка идеи взаимодействий.
- NULL v0.17 опровергает только узкую гипотезу «6 continuous features + 5 ручных interactions + linear ridge дают стабильный OOS-сигнал», а не DENN как нелинейную динамическую систему F(WorldState).
- Расширение событий (CPI/NFP/FOMC) сейчас НЕ приоритет: бутылочное горлышко — бедный world-state.

### Grouped suite v0.18 (первый прогон)
- Протокол заморожен в `config/grouped.yaml` (`denn-grouped-1`): блоки на ТЕКУЩЕЙ матрице — rates [2Y,10Y], energy [Brent,WTI], risk [VIX], fx_state [momentum]; family = 4 leave-one-block-out ablations, Bonferroni 0.05/4 = 0.0125; diagnostic-слои (вне семьи): block permutation importance (shuffled all members together) и within-block pairwise correlations; cross-reference с суммой single-LOO дельт v0.17 (descriptive gap = redundancy/synergy).
- Код: `src/fxlab/denn/grouped.py`, CLI `denn-grouped`, тесты `tests/test_grouped.py`. Baseline-якорь: воспроизведение aggregate v0.14 обязано совпасть с `denn_baseline` до 1e-9.
- Гипотезы (до просмотра): H1 — rates-блок имеет положительный block-ablation delta хотя бы на длинных горизонтах, превышающий сумму его single-LOO дельт (совместный латентный вклад); H2 — within-block корреляция 2Y/10Y и Brent/WTI высока (>0.9), подтверждая «две проекции» паттерн.
