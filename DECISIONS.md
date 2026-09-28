# Принятые решения

Дата: 2026-09-23. Основание: ответы пользователя после исходной спецификации.

## Объём и среда

- Самостоятельный исследовательский проект; сначала небольшой сквозной MVP, 5–10 факторов.
- Ubuntu-first, сервер Hermes, `192.168.88.15`, каталог `/home/alex/fx-causal-lab`.
- Python 3.12, Docker Compose, Parquet и DuckDB. PostgreSQL и OpenSPG отложены.
- Веб-интерфейс нужен сразу: отчёты, данные, визуализации. CLI сохраняется.
- Исходное окно 2023-09-23–2026-09-23; после отладки расширение до 8–10+ лет.
- Основные горизонты: 1, 5, 20, 60 торговых дней. H1 — дополнительный уровень.
- Публичные источники первыми. Платный consensus опционален; отсутствующие значения остаются null.

## Развёртывание

- Отдельный контейнер на `http://192.168.88.15:8088`, без изменения существующего Caddyfile.
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

- Пререгистрированная спецификация в `config/timing_audit.yaml` (version `denn-timing-audit-1`): мир-снапшот модель публикаций, 5 decision-time cutoff'ов (07:00 / 15:00 / 16:30 / 19:30 / 21:00 NY, DST-aware), зарегистрированные лаги 1/2/5/20, Bonferroni 0.05/4 = 0.0125, circular moving-block residual bootstrap (block = 20 sessions, 1000 повторов, seed 20260925, p только для best registered lag), sign stability по половинам, placebo сдвиг фактора на 20 сессий, exploratory scan ±10 сессий (только для отчёта).
- Модель публикаций (документированные допущения): US Treasury 15:30 ET, EA AAA кривая 18:50 NY (base case), ECB reference 16:00 CET, Brent/WTI EIA +1 день, VIX 15:15 ET. Фактор с grid-датой t доступен для решения на дате D только если его effective publication строго раньше cutoff(D); для каждого решения берётся последний доступный фактор.
- Итог по кандидату v0.16 (`SPREAD_2Y_CHANGE`): naive lag 1 r = −0.198, p = 0.001 (полное воспроизведение v0.16); при строгих cutoff'ах (strict_07, before_treasury_15, after_us_1630) best registered lag = 5, r = −0.031, block-bootstrap p = 0.028 > 0.0125 — не проходит. Сигнал появляется только в cutoff'ах после 19:30 (after_ea_1930, end_of_day_21), где доступна вечерняя EA AAA кривая, — точное воспроизведение naive статистики. Вердикт: `timing_artifact`, confirmed = False.
- Интерпретация: «lag +1» v0.16 фактически использует EA кривую, опубликованную вечером того же grid-дня (после завершения NY сессии). В рамках строгой PIT-рамки заявка не воспроизводится; при вечернем окне доступности связь сохраняется.
- Контроли после moving-block пересчёта: `SPREAD_10Y_CHANGE` — timing_artifact (strict p = 0.155); `VIX_RETURN` — timing_artifact (strict p = 0.179); `BRENT_CHANGE` и `WTI_CHANGE` — not_reproducible (strict p = 0.038 и 0.103; ни один не проходит порог 0.0125).
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
- **Исправленная permutation diagnostics:** первоначальная таблица ошибочно сопоставляла intercept с первым признаком и сдвигала остальные подписи на одну позицию. После исправления и перестановок внутри каждого walk-forward test fold `spread_2y_z60` имеет +1.69% / +2.32% / +3.60% / +6.24% dMSE на 1d/5d/20d/60d. `spread_10y_z60`: +0.39% / +0.06% / −1.20% / −3.00%. Поэтому прежний вывод «ridge обнуляет 2Y» отозван. Формальная LOO/interactions семья и её NULL-вердикт не изменились; permutation importance остаётся diagnostic-only.
- **Regime-срез:** ни один из 4 pre-registered режимов (VIX elevated/calm, spread wide/narrow) не показывает положительного skill: все skill_vs_mean отрицательны (1d: −0.84%/−0.56%/−0.21%/−0.23%; 20d elevated_vol −22.5% — худший). Линейная модель не имеет skill ни в одном режиме; это расширяет v0.14 null control до regime-условного уровня.
- **Ни один из 12 вариантов не проходит Bonferroni 0.00417 — ни положительный, ни отрицательный.** Ближайшие near-miss'ы (только нескорр. α=0.05): joint 1d (p=0.049) и oil_x_rate_spread 1d (4/17 в пользу, p=0.049, dMSE +3.4e-8) — оба в направлении ухудшения, т.е. знак против полезности. Значимых положительных результатов нет.
- **Permutation importance (diagnostic-only, исправлено 2026-09-26):** перестановки выполняются внутри test fold, чтобы не смешивать значения, стандартизованные разными train-fold параметрами. Наибольшая положительная чувствительность среди исходных признаков: 2Y spread +6.24% на 60d, WTI +5.45% на 1d, EUR/USD momentum +4.01% на 20d. Слой вне Bonferroni-семьи; выводы по полезности — из paired ablation deltas.
- **Следующие шаги:** (1) DENN memory-features ablation (baseline vs baseline+age/decay) — следующий пункт v0.17; (2) nonlinear multivariate baseline (LightGBM/RF) как слой 5 стека — требует sidecar-окружение с numpy/sklearn, контейнер core не имеет их; (3) temporal/lagged interactions (слой 8) — lag'и терминов, а не только level; (4) multivariate wavelet coherence (слой 9).

## 2026-09-26 — v0.18: переход к economic blocks; пререгистрация grouped suite

**Checkpoint:** NULL v0.17 заморожен как исторический чекпоинт — поправки к DECISIONS.md (исправление формулировок о Bonferroni/permutation) и результат `denn_state_vector.json` закоммичены в `eb26ef0` ДО каких-либо новых экспериментов. Секция v0.17 не изменяется; новые гипотезы ниже зафиксированы до просмотра результатов grouped-прогона.

### Директива (user, 2026-09-26)
- Единица анализа поднимается со столбца до **экономического блока**. Исходная мотивация использовала положительный LOO-ablation 2Y и ошибочно подписанную 0% permutation importance; audit v0.19 отозвал вторую часть, но grouped-анализ остаётся валидным самостоятельным тестом совместного вклада rates / policy differential.
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
- Пререгистрация закоммичена в `6bfd2f8` ДО запуска; прогон `python -m fxlab denn-grouped` (Docker, образ `fx-causal-lab:v018-grouped-test`), отчёт `data/reports/denn_grouped.json` (gitignored, как остальные).

### 2026-09-26 — Grouped suite v0.18: результаты (NULL, диагностиками подкреплённый)
**Результат: ни один блок не проходит Bonferroni 0.0125. NULL подтверждён вторым, независимым сьютом.**

Block-ablation (dMSE = изменение MSE при удалении ЦЕЛОГО блока; 17 folds на горизонт):

| горизонт | rates | energy | risk | fx_state |
|---|---|---|---|---|
| 1d | +2.86e-07 (CI не пересекает 0, 13/17, p=0.049) | −2.55e-07 | −4.7e-08 | +8.4e-09 |
| 5d | +5.91e-07 (CI через 0) | −1.82e-06 | −6.6e-07 | −2.0e-07 |
| 20d | −6.36e-06 | −1.04e-05 | −8.5e-06 | −8.1e-06 |
| 60d | +3.74e-05 (11/17, p=0.33) | −4.42e-05 | −5.3e-06 | −6.5e-06 |

**Диагностики (вне Bonferroni-семьи):**
- Block permutation importance (одна общая перестановка строк для всех колонок блока внутри каждого test fold; 1d): rates **+3.08%** MSE, energy +1.12%, fx_state +0.78%, risk +0.08% — модель больше всего опирается на rates-блок как на единицу. На 5d/20d/60d rates = +3.18%/+1.98%/+3.18%; это описательная диагностика, не формальный тест.
- Within-block корреляции (pooled OOS, n≈3000–4000): **2Y↔10Y r≈0.59–0.64**, **Brent↔WTI r≈0.85–0.86**.
- Cross-reference (block delta − сумма single-LOO дельт v0.17): gap = 0.000 в точности для одно-членных блоков (самопроверка реализации); для rates: 1d +1.5e-07, 5d +2.4e-08, 20d −0.8e-06, **60d +5.9e-05**.
- **Ключевой описательный факт (60d):** сумма single-LOO дельт 2Y+10Y ОТРИЦАТЕЛЬНА (−2.2e-05; каждая колонка по отдельности — шум, v0.17), а block delta ПОЛОЖИТЕЛЬНА (+3.7e-05). Блок несёт то, чего нет ни у одной колонки.

**Гипотезы:** H1 — НЕ подтверждена статистически (p=0.33 на 60d), но направленно подтверждена: совместный вклад есть, статистической мощности на 17 folds не хватает. H2 — НЕ подтверждена: r rates ≈ 0.6 (не >0.9), r energy ≈ 0.86 (высокая, но <0.9). «Две проекции одного фактора» — скорее умеренная, чем сильная, коллинеарность; Brent/WTI ближе к границе.

**Вывод для доктрины:** grouped-метод работает и различает «блок как единицу» от «сумма одиночных», но на текущей 6-колоночной матрице ни один блок не даёт значимого вклада — NULL устойчив через оба сьюта (single-feature v0.17 и block v0.18). Это усиливает аргумент, что следующий шаг — расширение world-state (Tier A данные), а не новые эксперименты на той же матрице. Ограничения прогона: non-strict (current-history без винтажей), блоки заморожены на текущей матрице.

## 2026-09-26 — Независимый audit серверной ветки и Tier A acquisition v0.19

- Четыре серверных коммита v0.17–v0.18 признаны соответствующими миссии: publication-time audit, multivariate state vector и economic-block ablation продолжают point-in-time исследование и не переходят преждевременно к Dynamic GNN.
- Исправлены три методологических дефекта: off-by-one в подписях single-feature permutation importance; глобальная перестановка стандартизованных значений между walk-forward folds; IID residual bootstrap вместо требуемого moving-block bootstrap. Повторный прогон сохраняет формальные вердикты: timing-кандидат 2Y остаётся `timing_artifact`, state-vector и grouped suites остаются NULL.
- Незакоммиченный прототип `tier_a.py` был полезен по цели, но технически непригоден: неверно разбирал H.4.1 ZIP/TIC/NFCI, смешивал календарный release age с индексом торговой сетки и не применял warm-up к coverage. Он заменён проверенным модулем `tier-a-world-state-2`.
- Фактически загружены и нормализованы 14 рядов: Fed 5Y/10Y inflation compensation и TIPS, NFCI/ANFCI, три H.4.1 liquidity ratio, два TIC holdings ряда, S&P 500, Euro Stoxx 50 и gold. Статус: 12 `READY_NON_STRICT`, 2 `PARTIAL_READY` (TIC с 2020), 0 unavailable. Все ряды остаются current-history/non-strict; отсутствующие значения не имитируются.
- Nullable Tier A feature matrix содержит 5 098 строк на frozen D1 grid (2004-12-17…2026-06-25). Доступные block ranges: inflation expectations с 2005-04-28; financial conditions с 2004-12-17; Fed liquidity с 2006-06-01; TIC с 2021-04-01; market proxies с 2007-05-04.
- Следующий gate: до просмотра результатов зарегистрировать expanded grouped suite на Tier A block ranges; для каждого теста использовать только его заявленный common interval и не заполнять пропуски.

## 2026-09-26 — Пререгистрация expanded Tier A grouped suite

- Протокол `denn-grouped-tier-a-1` заморожен в `config/grouped_tier_a.yaml` до первого прогона на реальной матрице.
- Одна common complete-case выборка без imputation; 16 признаков объединены в 7 блоков: rates, energy, risk/financial conditions, FX state, inflation expectations, Fed liquidity и cross-asset. Формальная семья: 7 leave-one-block-out ablations, Bonferroni 0.05/7; fold-local block permutation и within-block correlations остаются диагностикой.
- TIC не включён: доступный интервал 2021+ не оставляет выборки после шестилетнего train burn-in и отдельного validation года. Age/decay признаки не включены, чтобы не смешивать расширение observed state с отдельной memory-ablation гипотезой.
- Критерий перехода: сначала сохранить полный NULL/positive результат этой фиксированной линейной модели; только затем запускать отдельную memory/decay ablation. Dynamic GNN по-прежнему заблокирован.

### Expanded Tier A grouped suite: результат

- Dataset `f961f3fb24b18a063caf`: 4 639 complete-case строк, 2007-05-04…2026-06-25; 14 walk-forward folds на каждом горизонте. Ни один из 7 блоков не проходит Bonferroni 0.00714 — формальный результат NULL.
- Полная 16-feature ridge-модель не превосходит historical mean: skill −0.68% / −3.41% / −14.22% / −21.44% для 1d/5d/20d/60d. Расширение observed state без дополнительной regularization/structure усиливает переобучение на длинных горизонтах.
- Ближайший положительный кандидат: rates на 1d, ablation ΔMSE +3.34e-7, 12/14 folds, sign p=0.01294; это не проходит зарегистрированный порог. Rates остаётся descriptively наиболее устойчивым блоком: fold-local permutation +3.43%/+3.21%/+0.79%/+3.67% на 1d/5d/20d/60d.
- Energy имеет отрицательную ablation delta на всех горизонтах; на 20d/60d удаление блока улучшает модель (p=0.057, вне зарегистрированного порога). Cross-asset даёт большую permutation sensitivity на 60d (+7.71%), но его ablation delta отрицательна; reliance модели не является полезным OOS contribution.
- Inflation expectations, financial conditions и Fed liquidity не дают зарегистрированного положительного эффекта в этой линейной спецификации. Это не исключает conditional/temporal вклад; следующая отдельная гипотеза — age/decay memory ablation, без изменения текущего NULL-результата.

## 2026-09-26 — Пререгистрация memory / age-decay ablation v0.20

- Протокол `denn-memory-ablation-1` заморожен в `config/memory_ablation.yaml` до первого запуска на реальной матрице. Выборка и 16 baseline features совпадают с expanded Tier A suite; отсутствующие значения не заполняются.
- Treatment добавляет 13 заранее заданных temporal features: шесть EMA-memory состояний из deterministic baseline и семь freshness-decay состояний для inflation expectations, NFCI/ANFCI и H.4.1. Half-life фиксирован до просмотра результата: 30 календарных дней для inflation expectations, 14 дней для weekly financial/liquidity observations.
- Формальная семья содержит четыре paired walk-forward теста, по одному на 1d/5d/20d/60d. Статистика fold-level = `baseline_mse - treatment_mse`; положительный знак означает улучшение memory-варианта. Exact sign test проходит только при p < 0.0125; bootstrap CI остаётся описательной оценкой размера эффекта.
- Ни half-life, ни набор признаков не оптимизируются по test folds. Эксперимент остаётся non-strict из-за current-history входов. Dynamic GNN не разрешён до сохранения этого результата и следующего review gate.

### Memory / age-decay ablation: результат

- Dataset `340e1d29e6a3b81a27b2`: 4 639 complete-case строк, 2007-05-04…2026-06-25, 14 annual walk-forward folds на каждом горизонте. Baseline в точности воспроизводит expanded Tier A aggregate metrics.
- Ни один горизонт не показывает положительного зарегистрированного результата. Средняя paired improvement (`baseline MSE − treatment MSE`) отрицательна: −6.91e-7 / −1.14e-5 / −1.76e-4 / −1.82e-3 для 1d/5d/20d/60d.
- Folds в пользу memory: 2/14, 5/14, 6/14 и 7/14; exact two-sided sign p = 0.01294/0.42395/0.79053/1.0. Ни один тест не проходит Bonferroni 0.0125. На 1d bootstrap CI полностью ниже нуля, но это описательная оценка и зарегистрированный порог не пройден.
- Aggregate treatment skill vs historical mean = −3.54%/−13.20%/−57.41%/−160.59% против baseline −0.68%/−3.41%/−14.22%/−21.44%. Простое добавление 13 temporal columns усиливает variance/overfitting, особенно на длинных горизонтах.
- Вывод ограничен этой фиксированной линейной ridge-спецификацией и current-history входами. Он не доказывает бесполезность temporal operators вообще, но отвергает прямой переход к Dynamic GNN на основании этих признаков. Следующий допустимый model gate — структурированное уменьшение размерности/regularization, проверяемое отдельно.

## 2026-09-26 — Пререгистрация fold-local block PCA v0.21

- Протокол `denn-block-pca-1` заморожен в `config/block_pca.yaml` до первого запуска на реальной матрице. Выборка, 16 входных признаков, горизонты и annual folds совпадают с expanded Tier A suite.
- Каждый из семи заранее определённых economic blocks сжимается в один first principal component correlation matrix. Means, scales и loadings оцениваются только на past rows соответствующего fold: отдельно на selection-train для выбора ridge lambda и на fit rows для финального test prediction.
- Формальная статистика по каждому горизонту — `full_16_feature_mse − seven_block_state_mse`; положительное значение означает пользу компрессии. Семья = четыре горизонта, Bonferroni threshold 0.0125, exact paired sign test. Explained variance каждого состояния — diagnostic, а не критерий post-hoc отбора.
- PCA является unsupervised linear compression и может потерять predictive направление с малой variance. Этот риск фиксируется до результата. Dynamic GNN остаётся заблокирован; положительный PCA результат поддержит экономическую block representation, отрицательный — необходимость другого regularization/data gate.

### Fold-local block PCA: результат

- Dataset `ba5206be60c9dfdee186`: 4 639 строк, 2007-05-04…2026-06-25, 14 folds на горизонт. Full-model control в точности воспроизводит expanded Tier A metrics.
- Семь block states улучшают pooled OOS MSE на всех горизонтах. Skill vs historical mean становится +1.14%/−0.16%/−3.49%/+0.22% на 1d/5d/20d/60d против −0.68%/−3.41%/−14.22%/−21.44% у полной 16-feature модели.
- Paired mean improvement положительна: +4.30e-7/+3.89e-6/+4.47e-5/+2.68e-4. Но folds в пользу compression = 7/14, 9/14, 10/14, 9/14; exact sign p = 1.0/0.42395/0.17957/0.42395. Ни один горизонт не проходит Bonferroni 0.0125; bootstrap CI также пересекает ноль.
- Mean explained variance: rates 79%, energy 94%, risk/financial 76%, inflation expectations 95%, Fed liquidity 43%, cross-asset 62% (FX singleton 100%). Один компонент хорошо описывает energy/inflation, но слишком груб для liquidity и cross-asset.
- Вывод: экономическая block representation directionally уменьшает переобучение и заслуживает следующей проверки regularization, однако её стабильность не доказана. Нельзя переходить к Dynamic GNN или называть этот результат торговым преимуществом. Следующий model gate — заранее зарегистрированная sparse/supervised regularization либо двухкомпонентная проверка только для заранее указанных многомерных блоков.

## 2026-09-26 — Эксперимент с автоматическим отбором показателей v0.22

- До первого расчёта зафиксирован файл `config/elastic_net.yaml`. Берём ту же таблицу из 4 639 дней и те же 16 показателей, чтобы результат можно было напрямую сравнить с предыдущими моделями.
- Для каждого проверочного года модель видит только более ранние годы. На предыдущем году она выбирает, насколько сильно сокращать набор данных; затем без перенастройки прогнозирует следующий год.
- Главный вопрос: стала ли ошибка меньше обычной модели со всеми 16 показателями и в скольких из 14 проверочных лет это повторилось. Отдельно сохраняем, сколько показателей осталось и какие из них модель выбирала чаще.
- Список допустимых настроек зафиксирован заранее. После просмотра результата его нельзя расширять ради более красивых цифр. Даже если средняя ошибка уменьшится, единичные удачные годы не будут считаться устойчивым результатом.

### Автоматический отбор: что получилось

- Проверено 4 639 дней и 14 отдельных проверочных лет. Новая модель уменьшила среднюю ошибку относительно обычной модели со всеми показателями на сроках 1, 5, 20 и 60 дней.
- Качество относительно простого прогноза по среднему стало +0.70%/−0.15%/−6.37%/−13.40%. Это заметно лучше прежних −0.68%/−3.41%/−14.22%/−21.44%, но на длинных сроках прогноз всё ещё проигрывает простому ориентиру.
- По годам улучшение повторилось 8/14, 8/14, 11/14 и 12/14 раз. Самая интересная картина — 60 дней: почти все годы лучше старой модели, но несколько плохих периодов остаются достаточно сильными, чтобы общий результат был отрицательным.
- Среднее число оставленных показателей: 9.3/8.0/11.4/12.7 из 16. Чаще всего сохранялись reverse repo ФРС, WTI, разница двухлетних ставок США и Европы и NFCI. Набор меняется в зависимости от срока прогноза, поэтому пока нельзя назвать один универсальный фактор.
- Практический вывод: сокращение шума работает лучше простого добавления новых колонок. Следующий разумный тест — нелинейная модель на том же неизменном наборе данных, с теми же годами проверки. Если она не улучшит картину, приоритет возвращается к расширению и улучшению самих данных.

## 2026-09-27 — место AnyJev в системе

- AnyJev проверяется как отдельная вероятностная голова над structured world state, а не как замена прямым числовым моделям.
- На хосте 15 нет GPU и достаточно памяти только для приложения, очереди и отчётов. Текущий Qwen расположен на отдельном GPU-узле и обслуживается `llama.cpp` из GGUF; его API не отдаёт внутренние состояния слоёв, необходимые AnyJev L2.
- Первый совместимый пилот использует отдельный Qwen3-8B Transformers/vLLM runtime на GPU-узле. Существующий Qwen3.8-27B сервис Hermes сохраняется.
- Для 1d/5d/20d/60d используются разные неизменяемые вопросы и головы. Их обучение, настройка вероятностей и проверка идут только по хронологии.
- Исторические тексты несут риск знания будущего из pretraining. Сначала LLM извлекает тон и темы; прямой прогноз по тексту проверяется отдельно и дополняется настоящими shadow-прогнозами после запуска.
- План и требования к воспроизводимости зафиксированы в `docs/ANYJEV_INTEGRATION_RU.md`.
- На GPU-узле установлен отдельный Qwen3-8B с 24 блоками и AnyJev 0.1.0. Реальный L2 smoke test прошёл; результат имеет только технический смысл, потому что использовал 12 искусственных меток.
- Первый проектный контракт `anyjev-fx-1` фиксирует четыре отдельные головы, порядок `down/flat/up`, минимум 300 настоящих меток на вопрос и формирование границ классов только по прошлой обучающей части.

## 2026-09-27 — Пререгистрация проверки сложных сочетаний v0.23

- До первого расчёта зафиксированы те же 16 показателей, 4 639 полных строк и горизонты 1/5/20/60 дней.
- Контроль — модель v0.22 с автоматическим отбором. Новая модель — деревья с градиентным усилением, способные находить изгибы и сочетания факторов.
- Для каждого проверочного года настройки обеих моделей выбираются только на предыдущем году. Проверочный год не участвует ни в выборе настроек, ни в обучении.
- Проверяются восемь заранее заданных сочетаний глубины, скорости обучения и ограничения сложности. Автоматическая остановка отключена, случайное зерно закреплено.
- Если новая модель не показывает повторяемого улучшения, перебор более сложных моделей на этой матрице прекращается. Основной приоритет переходит к длинному рыночному EUR/USD и расширению CFTC/EIA/macro history.

### Проверка сложных сочетаний: что получилось

- Проверено 4 639 дней и 14 отдельных лет для каждого срока. Новая модель оказалась хуже автоматического отбора на 1, 5, 20 и 60 дней.
- Результат относительно простого среднего: −1.24%, −12.11%, −26.51% и −56.01%. У модели v0.22 было +0.70%, −0.15%, −6.37% и −13.40%.
- Новая модель была лучше старой только в 3/14, 2/14, 3/14 и 4/14 лет. Наиболее часто прошлые годы выбирали самый простой из разрешённых вариантов дерева, но даже он не помог.
- Вывод: в текущей таблице нет убедительной скрытой пользы, которую можно извлечь простым усложнением модели. Следующий этап — длинный рыночный EUR/USD, затем расширение CFTC и EIA.

## 2026-09-27 — Расширение открытых данных v0.24

- Проверен месячный архив Dukascopy за сентябрь 2004 года: официальный JSON endpoint вернул корректно закодированные H1 свечи. Существующий адаптер можно использовать для длинной истории.
- Для CFTC найден официальный объединённый TFF Futures Only архив 2006–2016. Адаптер расширен для старого формата дат и чисел; фактическая проверка дала 551 строку EUR FX с 2006-06-13 по 2016-12-27.
- Для EIA выбраны два официальных недельных ряда без API-ключа: коммерческие запасы нефти без SPR с 1982 года и добыча нефти с 1983 года.
- EIA-файлы являются нынешней версией истории. Старые точные времена публикации в них отсутствуют, поэтому для первого исследования значение считается доступным со следующей пятницы и помечается как консервативная оценка.
- Полная загрузка Dukascopy за 2004-09-06–2026-09-27 дала 137 737 H1 и 5 744 NY17 D1 строк. Из 5 744 дней 5 732 полные. Найден 21 отсутствующий час, 13 из них соответствуют неверным OHLC от поставщика; они исключены, а не исправлены догадкой. Дубликатов и нулевых объёмов нет.
- Офлайн-повтор из 266 сохранённых исходных снимков дал тот же dataset `941ef95ac00cb144cc55` и SHA-256 `7e99d39e6c028acaa2c2aab3681ec77527c72cff676f964ba4248c871c5e99b2`.
- Пути файлов в манифесте записываются через `/`, чтобы один и тот же манифест работал на Windows и Ubuntu.

## 2026-09-27 — План проверки на рыночном EUR/USD v0.25

- До просмотра результата зафиксирована отдельная общая таблица с Dukascopy NY17 D1. Она содержит 5 206 дней против 5 217 дней в прежней таблице со справочным курсом ECB.
- Проверка повторяет заранее выбранные задержки 0, 1, 5 и 20 дней для разницы ставок 2Y/10Y, Brent, WTI и VIX. Оба курса сравниваются только на одинаковых датах.
- Главная проверка: изменение разницы двухлетних ставок сегодня и EUR/USD на следующий день. Старый результат считается подтверждённым только если знак остаётся отрицательным, величина связи не меньше 0.05, а отрицательный знак сохраняется как минимум в 75% последовательных четырёхлетних окон.
- Это проверка устойчивости описанной связи, а не доказательство причины и не торговая стратегия. Результат публикуется независимо от того, подтвердится старая картина или нет.

### Проверка на рыночном EUR/USD: что получилось

- В сравнении осталось 5 205 дневных изменений с 2004-09-09 по 2026-09-22 и 18 последовательных окон примерно по четыре года.
- Дневные изменения справочного курса ECB и рыночного закрытия Dukascopy имеют корреляцию 0.511 и одинаковое направление в 66.3% дней. Среднее абсолютное расхождение дневных движений — 41.6 пункта. Это существенная разница в определении «дня».
- Главная связь `2Y spread change → EUR/USD next day` равна −0.009 на Dukascopy против −0.198 на ECB. Отрицательный знак сохранился в 66.7% окон при заранее установленном требовании 75%. Вердикт: `NOT_CONFIRMED`.
- На нулевой задержке связь с 2Y spread равна −0.252 и отрицательна во всех 18 окнах; для 10Y — −0.174 и также отрицательна во всех окнах. Это движение внутри одного измеряемого дня. Его нельзя использовать как прогноз после закрытия дня.
- Практический вывод: прежний результат с задержкой в один день был чувствителен к источнику и времени фиксации курса. Дальше проверяем точное временное выравнивание и более длинные горизонты на Dukascopy, а не усиливаем старый вывод.

### Полные локальные CFTC и EIA

- CFTC EUR TFF собран за 2006-06-13–2026-09-22: 1 059 недельных строк. Для 1 020 старых строк точная дата доступности неизвестна, поэтому они не допускаются в строгие эксперименты. Данные и контрольная сумма повторены из сохранённого манифеста без сети.
- EIA stocks и U.S. crude production собраны за 2004-01-02–2026-09-18: по 1 186 недельных строк, всего 2 372. Офлайн-повтор дал тот же dataset `1093478fb3f52833f42d`.
- Все относительные пути в новых и затронутых манифестах приведены к единому виду с `/`, чтобы локальный пакет переносился на Ubuntu без ручного исправления.
# 2026-09-27 — Communications data and two Qwen roles

- EA-CED is the first communications research dataset: it provides broad ECB/Eurosystem event coverage, Bloomberg-reported event times, cleaned intraday market reactions and a large ECB speech text collection.
- Event selection must be independent of the observed EUR/USD move. Famous events can be case studies, but they cannot define the training sample.
- Complete historical speech text is retrospective evidence until its exact `available_at` is known. It may be used for topic/tone research, but not silently treated as a pre-event input.
- The existing Qwen3.8-27B `llama.cpp` service is retained and used without reload for an L0/L1 comparison based on option token probabilities.
- The separate Qwen3-8B checkpoint is retained for AnyJev L2 because it exposes the intermediate hidden state required by that method and fits the RTX 3090.
