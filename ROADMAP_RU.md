# FX Causal Lab — выполненная работа и исследовательский роадмэп

Статус документа: рабочий план после v0.16  
Дата среза: 2026-09-25  
Основной объект: EUR/USD  
Горизонты: 1, 5, 20 и 60 торговых сессий  
Среда: Ubuntu, Python 3.12, Docker Compose, DuckDB/Parquet, CLI и веб-отчёты

## 1. Цель проекта

Цель — построить воспроизводимую исследовательскую систему, которая проверяет экономические гипотезы о EUR/USD без look-ahead, revision leakage и искусственного заполнения отсутствующих данных. Система должна уметь получить нулевой результат и сохранить его как нормальный итог эксперимента.

Целевой путь:

```text
официальные и публичные источники
    → неизменяемые raw snapshots
    → нормализованные point-in-time сущности
    → общая временная сетка и event alignment
    → статистические и temporal baselines
    → проверка устойчивости связей
    → causal hypothesis graph
    → только затем dynamic graph / world model
```

## 2. Как читать статусы

| Статус | Значение |
|---|---|
| DONE | Реализовано, протестировано, воспроизводится офлайн и опубликовано в веб-интерфейсе. |
| PARTIAL | Данные или код пригодны для non-strict исследования, но не прошли один из PIT, coverage, licensing или market-semantics gates. |
| BLOCKED DATA | Схема и эксперимент определены, однако обязательных данных нет. Искусственная подмена запрещена. |
| PLANNED | Работы ещё не начаты или есть только source candidate. |

`DONE` относится к инженерному результату. Он не превращает current-history данные в исторические vintages и не означает доказанную причинность.

## 3. Что уже сделано

### 3.1 Инженерная платформа

| Компонент | Статус | Результат |
|---|---|---|
| Ubuntu deployment | DONE | Сервис работает в Docker на `192.168.88.5:8088`; контейнер без root, read-only filesystem, отдельный data mount. |
| CLI | DONE | Backfill, replay, normalization, alignment, experiments, graph, DENN и spectral-команды запускаются отдельно. |
| Web research journal | DONE | Данные, quality reports, experiments, граф и downloadable manifests/Parquet доступны в браузере. |
| Bronze / Silver / Gold | DONE | Raw snapshots отделены от нормализованных и исследовательских datasets. |
| Content identity | DONE | Raw и normalized outputs идентифицируются SHA-256; dataset ID не зависит от времени запуска. |
| Offline replay | DONE | Основные datasets воспроизводятся в Docker с `--network none`. |
| Provenance schema | DONE | Хранятся source, URL, payload hash, source snapshot, units, revision ID и quality. |
| Time schema | DONE | `event_time`, `published_at`, `available_at`, `ingested_at` разделены. |
| PIT filter | DONE | `unknown` исключается из строгих экспериментов; conservative timestamps не выдаются за exact. |
| Consensus schema | DONE | Поля actual, consensus, previous, revised_previous, surprise, normalized_surprise и forecast vintage готовы; отсутствующие значения остаются null. |
| Backup и scheduled ingestion | PLANNED | Persistent data mount есть, но он ещё не является резервной копией; cron/monitoring не включены. |

### 3.2 Реализованные этапы и версии

| Этап | Версия | Статус | Что доказано |
|---|---:|---|---|
| Начальный ECB reference pipeline | v0.1 | DONE | Raw snapshot, normalization, replay, web и базовая PIT schema работают сквозным образом. |
| EUR/USD H1/D1 quality | v0.2 | PARTIAL | 18 667 валидных H1, 780 NY17 D1, 13 invalid OHLC в quarantine; три года истории. Historical immutability не доказана. |
| BLS CPI/NFP archive | v0.3 | PARTIAL | 70 releases и 105 headline observations с официальными embargo timestamps. `available_at` и consensus отсутствуют. |
| CFTC EUR TFF | v0.4 | PARTIAL | 159 weekly rows; 38 conservative availability, 121 unknown. |
| FOMC statements | v0.5 | PARTIAL | 25 решений с официальным release time и target range; historical receipt не доказан. |
| ECB policy decisions | v0.6 | PARTIAL | 25 решений с FOEDB timestamp и key rates; historical receipt не доказан. |
| Event alignment | v0.7 / M4 | DONE non-strict | 267 строк, три prediction modes, targets 1/5/20/60d и anti-leakage boundary. Strict PIT rows: 0. |
| Event baseline | v0.8 / M5 | DONE non-strict | 28 unconditional summaries, HAC и FDR; опубликованные surprise effects зарегистрированы как unavailable. |
| Causal hypothesis graph | v0.9 / M6 | DONE | YAML/NetworkX DAG: 18 nodes, 18 edges, семь priority chains, JSON и GraphML. |
| Interaction slices | v0.10 / M7 | DONE non-strict | Trend и positioning regimes, 88 summaries; missing inputs не подменены. |
| Readiness review | v0.11 | DONE | Evidence gates и data-unlock priorities собраны в одном отчёте. |
| CPI/NFP tick prototype | v0.12 | DONE non-strict | Event anchors, 126 811 ticks на control day, M1 aggregation и spread checks; vendor consensus отложен. |
| Free/public coverage | v0.13 | DONE non-strict | Treasury/ECB yields, spreads, Brent, WTI, VIX и ECB EUR/USD сведены в common D1 dataset. |
| DENN deterministic baseline | v0.14 | DONE non-strict | 41 736 snapshots, 5 098 feature rows, 68 purged annual folds, 14 548 OOS predictions. |
| Spectral baseline | v0.15 | DONE non-strict | FFT, Welch coherence/phase, Haar energy и lag scan на 5 216 aligned changes. |
| Spectral stability | v0.16 | DONE non-strict | 18 rolling и 18 expanding окон; fixed bands и registered lags 0/1/5/20. |

### 3.3 Текущее покрытие данных

| Набор | Частота | Покрытие | Текущее применение | Главный пробел |
|---|---|---|---|---|
| ECB EUR/USD reference | D1 | 2004-09–2026-09 | Длинная continuous сетка | Не executable OHLC; historical availability unknown. |
| Dukascopy EUR/USD | H1 / NY17 D1 | Около 3 лет | Market quality и event prototype | Нужна длинная история, условия использования и archive immutability. |
| U.S. Treasury 2Y/10Y | D1 | 2004+ | Spread features и spectral analysis | Нужен точный daily cutoff; current history non-strict. |
| ECB euro-area AAA 2Y/10Y | D1 | 2004-09+ | Spread features и spectral analysis | Не sovereign German yield; timing и vintage semantics требуют проверки. |
| US–EA 2Y/10Y spreads | D1 derived | Общий inner join | DENN/spectral factors | Наследуют ограничения обеих сторон. |
| Brent / WTI | D1 | Длинная публичная история | Oil changes и spectral factors | Spot history не заменяет energy fundamentals или futures expectations. |
| VIX | D1 | Длинная публичная история | Risk factor | Current-history, без historical vintage proof. |
| BLS CPI/NFP | Event/monthly | 2023-09–2026-08 | Event alignment | Коротко; нет consensus и historical receipt. |
| FOMC / ECB decisions | Event | 2023-09–2026-09 | Event alignment | Нет policy surprise factor и historical receipt. |
| CFTC EUR TFF | Weekly | 2023-09–2026-09 | Positioning regimes | Коротко; 121 unknown availability; Futures Only. |
| US/European equities | D1 | Нет интегрированного ряда | Недоступно | Нужно выбрать публичные proxies и реально скачать историю. |
| Gold | D1 | Нет интегрированного ряда | Недоступно | Нужен публичный источник с понятными terms. |
| EIA fundamentals / TIC / credit | Weekly/monthly/D1 | Нет интегрированных рядов | Недоступно | Требуются adapters, release calendars и provenance. |
| Historical consensus | Event/vintage | Нет | Недоступно | Optional paid source; не блокирует continuous MVP. |

### 3.4 Полученные исследовательские результаты

1. Линейный ridge baseline на шести continuous features не превзошёл expanding historical mean ни на одном горизонте. Skill: −0.01%, −1.47%, −5.80% и −0.80% для 1d/5d/20d/60d. Это сохранённый null control.
2. Самая высокая full-sample mean coherence равна 0.245 для изменения US–EA 2Y spread в полосе 30–90 sessions. Phase показывает обратный порядок: EUR/USD опережает spread примерно на 41 session.
3. Full-sample lag scan дал −0.198 для 2Y spread changes при lag +1. В 18 rolling windows знак остался отрицательным; median −0.203, Q10/Q90 −0.290/−0.137.
4. Full-sample spectral band не оказался устойчиво доминирующим: ни одна rolling modal-band share не превышает 44.4%.
5. Эти наблюдения пока не являются causal или trading results: rolling-окна перекрываются на 75%, expanding-окна вложены, yields и ECB reference имеют неполные timing semantics, а inputs не являются historical vintages.

## 4. Definition of Done для нового источника

Источник считается интегрированным только после выполнения всех применимых пунктов:

1. Зафиксированы официальный URL, документация, authentication, rate limits, cost и условия хранения/derived use.
2. Реальный payload скачан и сохранён без ручного редактирования.
3. Создана receipt metadata: request URL/parameters, `ingested_at`, HTTP metadata и SHA-256.
4. Parser проверяет schema, units, duplicates, ordering, missing values и невозможные значения.
5. Создан нормализованный Parquet со стабильным ID ряда и единицами измерения.
6. Разделены observation/event time, publication time, historical availability и ingestion time.
7. Revisions представлены отдельными vintages; final revised value не появляется раньше публикации revision.
8. Неизвестное время получает `unknown`; conservative delay хранится как `inferred_conservative`.
9. Построен coverage report: from/to, row count, missing share, duplicate count и общий overlap.
10. Offline replay проверяет raw hashes и воспроизводит тот же normalized hash.
11. Определено, разрешён ли ряд для strict PIT, non-strict research или только source reconnaissance.
12. Manifest и Parquet доступны в веб-интерфейсе; ограничения видны рядом с данными.

HTTP 200, документация API или metadata без скачанных наблюдений не закрывают интеграцию.

## 5. Приоритетный план сбора данных

### D0. Надёжность хранилища и каталог экспериментов

Приоритет: немедленно. Зависимостей нет.

- Добавить versioned dataset catalog: dataset ID, parent IDs, config hash, code commit, row counts и quality gates.
- Создать резервное копирование `data/bronze`, manifests и Gold results с проверкой восстановления.
- Добавить dry-run и lock для будущего scheduled ingestion.
- Создать experiment registry: hypothesis ID, frozen inputs, prediction time, target, split, metrics и multiplicity family.
- Сохранить machine-readable roadmap/readiness status, чтобы веб-страница не расходилась с manifests.

Выходной gate: любой опубликованный отчёт можно восстановить из raw snapshots, config и commit без сети.

### D1. Continuous market core

Приоритет: первый data milestone.

| Ряд | Предпочтительный путь | Частота | Требуемая история | Что проверяем | Что открывает |
|---|---|---:|---:|---|---|
| EUR/USD executable proxy | Продолжить Dukascopy либо подключить MT5/provider interface | H1 + D1 | 2004/2005+ желательно | UTC, bid/ask, NY17 sessions, DST, gaps, spread, revisions, terms | Проверка результатов ECB reference на tradable prices; H1 event reactions. |
| U.S. equities | Публичный официальный/биржевой index или устойчивый total-return proxy | D1 | 2004+ | Close semantics, dividends, calendar, redistribution | Relative-equity и risk-on/off factors. |
| European equities | Публичный STOXX/широкий proxy с допустимыми terms | D1 | 2004+ | Сопоставимость с US proxy, currency и close time | US–EU relative performance. |
| Gold | Публичный benchmark или futures proxy | D1 | 2004+ | Fix/close time, units, current history | Safe-haven/inflation factor. |
| Credit / financial conditions | FRED public CSV или официальный provider | D1/weekly | 2004+ | Underlying terms, vintage status, release timing | Risk/liquidity regime. |

Для длинного EUR/USD priority выше, чем для новых exotic factors: без независимого tradable-price ряда нельзя считать найденный lag +1 надёжным.

Выходной gate: общий continuous dataset содержит EUR/USD, 2Y/10Y spreads, oil, VIX, US/EU equities и gold с измеренным overlap не менее десяти лет; ни один ряд не forward-filled через неизвестное значение.

### D2. Rates и monetary expectations

Приоритет: второй data milestone; частично зависит от D1.

- Уточнить timestamp/cutoff Treasury и ECB yield observations относительно ECB FX reference.
- Проверить сопоставимость ECB AAA curve с альтернативным German/euro-area 2Y/10Y proxy.
- Для event experiments найти intraday U.S. и euro-area rate proxies: futures/OIS либо ликвидные instruments с H1/M1 history.
- Отдельно строить level, daily change, curve slope и US–EA differential.
- Не использовать фактическое изменение policy rate как замену surprise.

Выходной gate: для каждого rates feature известно, какая информация была доступна до prediction time; reaction-confirmed target начинается после окна измерения rates.

### D3. Official macro releases и vintages

Приоритет: параллельно D1/D2.

| Блок | Источники | Минимальный набор | Целевая глубина |
|---|---|---|---:|
| U.S. prices/labour | BLS | CPI headline/core, PPI, NFP, unemployment, wages | 10+ лет releases |
| U.S. activity/income | BEA, Census | PCE, GDP, retail sales, trade balance | 10+ лет releases |
| Euro area | Eurostat, ECB | HICP, GDP, unemployment, wages/compensation | 10+ лет releases |
| Surveys | Official/public candidates; PMI только при допустимой лицензии | Business/consumer confidence, PMI candidate | Максимум доступной легальной истории |

Для каждого релиза нужны release ID, reference period, actual-as-released, previous, revised_previous, published_at, available_at quality и revision lineage. Latest current value не заменяет first release.

Выходной gate: как минимум CPI, NFP, PCE и HICP имеют 8–10+ лет first-release observations; исключения и corrections отражены явно.

### D4. Flows, positioning и energy fundamentals

Приоритет: после stable continuous core, но до interaction claims.

- Расширить CFTC EUR TFF назад; восстановить фактические release dates, holiday delays и corrections.
- Отдельно оценить Futures Only и Futures + Options; не смешивать определения.
- Добавить open-interest normalization и изменения positioning, сохраняя raw categories.
- Интегрировать Treasury TIC flows с publication calendar и revision fields.
- Интегрировать EIA inventories, U.S. production и WPSR release schedule.
- Добавить открытые OPEC production series только после проверки archival consistency и terms.

Выходной gate: positioning/flow feature имеет `available_at <= prediction_time`, достаточную expanding history и не использует report date как publication date.

### D5. Consensus и premium data

Приоритет: optional; не блокирует D0–D4 и continuous experiments.

- Запросить одинаковый CPI/NFP sample у Econoday и Trading Economics.
- Проверить actual-as-released, final pre-release consensus, contributor count, revision timestamps и forecast vintage.
- Зафиксировать стоимость, API limits, storage rights, derived-research rights и историю по series/country.
- LSEG и Bloomberg оставить enterprise alternatives.
- При отсутствии приемлемого provider сохранить experiment status `unavailable`; consensus не восстанавливать из realized actual или market move.

Выходной gate: sample replay доказывает, что forecast существовал до release и не был перезаписан последующим vintage.

## 6. Программа следующих экспериментов

### E0. Инженерные null controls — выполнено

- Historical-mean baseline.
- Purged expanding ridge.
- Unconditional event returns с HAC/FDR.
- Full-sample spectral diagnostics и rolling/expanding stability.

Результат: сложная модель ещё не оправдана; текущий ridge не имеет положительного skill.

### E1. Confirmation suite для rates → EUR/USD

Цель: проверить, переживает ли найденная отрицательная lag +1 связь корректировку времени и независимую проверку.

Входы: EUR/USD, US–EA 2Y/10Y changes, точные daily cutoffs.  
Метод:

1. Заморозить discovery interval и более поздний confirmation interval.
2. Повторить только зарегистрированные lag 0/1/5/20 без повторного поиска максимума.
3. Использовать non-overlapping windows в дополнение к текущим overlapping windows.
4. Получить block-bootstrap/HAC intervals и circular/block-shift placebo distribution.
5. Проверить календарные сдвиги −2…+2 дня как timing sensitivity, но считать их отдельной multiplicity family.
6. Повторить на tradable EUR/USD D1 после D1; ECB reference оставить robustness source.
7. Проверить стабильность до/после 2008, euro crisis, 2020 и inflation hiking cycle без подгонки индивидуальных правил.

Success gate: знак и величина не разваливаются на confirmation interval и альтернативном EUR/USD source. Даже тогда результат называется predictive association, пока causal design не выполнен.

### E2. Frozen temporal baselines

Цель: понять, дают ли continuous factors добавочную OOS-информацию на 1/5/20/60d.

Модели по порядку:

1. Historical mean / zero return / autoregressive baseline.
2. Linear и ElasticNet.
3. Logistic/ordinal heads для return buckets.
4. LightGBM/XGBoost только после фиксации feature set и tuning protocol.
5. TFT/TCN только если простые модели дают устойчивый lift или выявляют конкретный nonlinear gap.

Признаки: spreads, curve slopes, oil, VIX, relative equities, gold, CFTC и age/decay; spectral bands добавляются отдельной ablation-группой.

Валидация: expanding/purged walk-forward, embargo для overlapping targets, preprocessing fit только на прошлом, tuning только во внутреннем historical validation window.

Метрики: MAE/MSE только как диагностика; основные — quantile loss/CRPS, log loss/Brier/ECE для buckets, information coefficient и regime stability. Direction accuracy вторична.

Success gate: повторяемый lift против frozen baseline минимум на нескольких последовательных folds с приемлемой calibration. Один удачный regime не считается общей победой.

### E3. Published macro-effect replication

Цель: прежде чем искать новые эффекты, воспроизвести известные macro surprise → FX/rates реакции.

Приоритетные события:

1. CPI US surprise → EUR/USD и U.S. rates.
2. NFP surprise → EUR/USD и U.S. rates.
3. FOMC futures/OIS surprise → rates → EUR/USD.
4. ECB policy surprise → euro-area rates → EUR/USD.
5. HICP/PCE surprises после появления достаточной истории.

Отдельные designs:

- pre-event: только информация до release;
- post-release: actual/surprise доступны после подтверждённого receipt;
- reaction-confirmed: rates/FX reaction window завершён до начала target.

Success gate: definition surprise, release timestamp, market window и sample максимально близки к статье; расхождения и non-replication публикуются. Без consensus/futures surprise experiment остаётся `unavailable`.

### E4. Mediation и local projections

Цель: проверить цепочки, а не только конечную корреляцию.

Примеры:

- CPI surprise → 2Y yield reaction → EUR/USD next 1–5d;
- FOMC surprise → curve repricing → EUR/USD;
- oil shock → inflation expectations/rates → EUR/USD;
- risk shock → VIX/equities/funding stress → EUR/USD.

Методы: event study, local projections, VAR/SVAR как sensitivity analysis, mediation decomposition с явными temporal boundaries. Rates first-hour feature разрешён только для target, начинающегося после этого часа.

Success gate: mediator измерен раньше target, direct/indirect definitions frozen, результаты устойчивы к альтернативным lag и sample definitions.

### E5. Interaction experiments

Цель: проверить, меняется ли реакция в зависимости от positioning и режима.

Заранее определённые взаимодействия:

- surprise × CFTC positioning;
- surprise × inflation/growth/risk regime;
- oil × inflation expectations;
- rate differential × risk sentiment;
- policy surprise × funding/liquidity regime.

До formal contrasts нужны достаточные samples в каждой ячейке. Single-observation и N<8 slices остаются только диагностикой. Multiplicity family и shrinkage задаются до просмотра результата.

### E6. Spectral features в прогнозе

Цель: проверить, добавляют ли wavelet/frequency representations OOS-полезность.

- Band definitions остаются frozen или выбираются только во внутреннем train interval.
- Coherence, phase и band energy вычисляются trailing-only.
- Сравнение проводится как ablation: temporal baseline без spectral features против той же модели с ними.
- Full-sample band maxima не передаются в historical fold как известные заранее.

Success gate: spectral block даёт stable OOS/calibration lift после multiplicity control. Иначе он остаётся explainability/research diagnostic.

### E7. Multi-task economic world model

Запускается только после E2–E6 gates.

Outputs: будущие rates, VIX, oil, relative equities и распределение EUR/USD. Начать с shared temporal encoder и независимых heads; сравнить с single-task baselines. Loss weights фиксировать на validation, а не на final test.

### E8. Static, затем dynamic graph model

Порядок:

1. Привязать каждый graph edge к evidence registry и available features.
2. Реализовать static masked graph head с фиксированной topology.
3. Сравнить с feature-equivalent non-graph model.
4. Добавить learned base edge weights и sparsity/stability regularization.
5. Только затем regime encoder → dynamic `Δw(t)` и temporal graph memory.

Каждый forecast сохраняет top nodes/edges, base/current weight, decay, horizon gate, ablation delta и uncertainty. Attention/edge weight не называется причинностью.

### E9. AnyJev и LLM readout — отдельная ветка

AnyJev допускается после появления structured world state и сильных прямых baselines. Используется как typed readout с calibration только на прошлом. Он сравнивается с logistic/linear/LightGBM/graph heads и не заменяет их.

## 7. Обязательные anti-leakage gates

| Gate | Проверка |
|---|---|
| Availability | Любой feature имеет `available_at <= prediction_time`; unknown исключён из strict run. |
| Revision | Используется только vintage, опубликованный к prediction time. |
| Forecast | Consensus имеет `forecast_vintage_at < published_at`; final consensus после события запрещён. |
| Reaction | Окно reaction feature завершено до target start. |
| Target | Target начинается после feature boundary; overlapping labels purged/embargoed. |
| Preprocessing | Scaling, imputation, feature selection и spectral parameters fit только на train/validation past. |
| Calendar | Неизвестные holidays/sessions не пропускаются молча и не заполняются. |
| Multiplicity | Семейство hypotheses и correction method задаются до чтения test result. |
| Source robustness | Ключевой результат повторяется на независимом provider/proxy, если доступен. |
| Null result | Отсутствие lift публикуется; pipeline не оптимизируется ради обязательной прибыли. |

## 8. Последовательность ближайших релизов

Это порядок зависимостей, а не календарное обещание.

| Релиз | Основной результат | Data work | Experiment work | Gate выхода |
|---|---|---|---|---|
| v0.17 | Research registry и rates confirmation protocol | Dataset/experiment catalog, backup design, yield cutoff audit | Non-overlap, block bootstrap, discovery/confirmation и timing placebo для registered lags | Понятно, является ли lag +1 устойчивым или timing artifact. |
| v0.18 | Continuous Coverage II | Long tradable EUR/USD, US/EU equities, gold | Повтор v0.14–v0.17 на расширенной matrix | 10+ лет общего overlap либо документированный narrower interval. |
| v0.19 | Probabilistic temporal baselines | Frozen feature catalog | Linear/ElasticNet и calibrated return buckets, purged walk-forward | Честное сравнение против mean/AR baselines. |
| v0.20 | Macro Archive II | BLS/BEA/Eurostat releases и revisions 8–10+ лет | First-release quality/revision diagnostics | Достаточная event sample и понятные vintages. |
| v0.21 | Policy expectations | Futures/OIS/rates reaction data, если легально доступны | FOMC/ECB surprise construction | Surprise существует до измеряемой реакции и воспроизводится offline. |
| v0.22 | Published Effects | Optional consensus sample либо официальный substitute только где методически допустимо | CPI/NFP/FOMC/ECB replications | Replicated / rejected / unavailable по каждой статье. |
| v0.23 | Mediation + interactions | CFTC/TIC/EIA expansion | Local projections и frozen interactions | Temporal boundaries и sample size gates проходят. |
| v0.24 | Static graph benchmark | Edge evidence registry | Masked static graph против feature-equivalent baseline | Graph даёт проверяемую добавочную ценность или отклоняется. |
| v0.25+ | Dynamic DENN candidate | Regime/latent confidence layer | Dynamic weights, multi-task heads, calibration | Только после успеха простых baselines и static graph. |

## 9. Решения после ключевых gates

### После v0.17

- Если lag +1 исчезает после cutoff correction или на non-overlap/confirmation sample, пометить его timing artifact/unstable и не превращать в feature prior.
- Если сохраняется, зарегистрировать знак и lag как candidate prior для v0.19, без causal label.

### После v0.19

- Если простые temporal models не дают OOS lift, не переходить автоматически к GNN. Сначала проверить data quality, target definition и regime-specific residual structure.
- Если lift локален одному regime, сформулировать отдельную ограниченную гипотезу и проверить следующий chronological interval.

### После v0.22

- Если опубликованные эффекты не воспроизводятся, сначала проверить release/vintage alignment и market timestamps.
- Если данные корректны, non-replication остаётся итогом; нельзя менять sample до получения желаемого знака.

### После v0.24

- Dynamic graph допускается только если static graph либо multi-task representation даёт измеримую ценность сверх тех же tabular features.
- OpenSPG подключается, когда graph/evidence registry стал операционно полезен; он не нужен для доказательства первой модели.

## 10. Что сознательно не является ближайшей задачей

- High-frequency trading и latency infrastructure.
- Автоматическое исполнение сделок.
- Оптимизация PnL до проверки statistical/calibration metrics.
- Покупка consensus как блокирующее условие проекта.
- GNN, TFT или LLM только ради сложности архитектуры.
- Публичное размещение сервиса без отдельного решения по домену и authentication.
- Смешивание pre-event, post-release и reaction-confirmed observations.

## 11. Критерий готовности Research MVP

Research MVP считается завершённым, когда одновременно выполнены следующие условия:

1. Continuous core имеет не менее 8–10 лет общего проверенного покрытия либо для каждого более короткого ряда указан честный interval.
2. Есть независимый tradable EUR/USD ряд для robustness, а ECB reference используется только в своей семантике.
3. Все datasets имеют provenance, quality, replay и coverage report.
4. Strict и non-strict experiments физически разделены и не смешиваются в одной метрике.
5. Mean/AR/linear/ElasticNet baselines и как минимум один nonlinear baseline прошли одинаковый purged walk-forward protocol.
6. Хотя бы несколько опубликованных эффектов получили статус replicated, rejected или data-unavailable по заранее заданным правилам.
7. Spectral features проверены trailing-only через ablation, а не выбраны по full sample.
8. Causal graph связан с evidence registry; неподтверждённые edges помечены hypothesis/untested.
9. Веб-отчёт показывает datasets, experiments, hashes, ограничения и null results.
10. Восстановление из backup и полный offline replay проверены на отдельном запуске.

При выполнении этих условий можно принимать отдельное решение о Dynamic DENN, OpenSPG, AnyJev и расширении интерфейса. Устойчивая прибыльная стратегия не является обязательным критерием успеха.

