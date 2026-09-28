# FX Causal Lab

Исследовательский MVP EUR/USD на Ubuntu: публичные источники, provenance, проверки доступности и веб-отчёты.

Версия 0.27.2: отдельный AnyJev/Qwen3-8B runtime установлен и проверен на RTX 3090. Добавлен стабильный контракт заданий для горизонтов 1/5/20/60 дней, защита от данных из будущего и зафиксирован compatibility patch для свежей конфигурации Qwen3. Текущий Qwen3.8-27B Hermes после теста автоматически восстановлен.

```bash
pip install -c requirements.lock '.[test]'
fxlab backfill --from 2023-09-23 --to 2026-09-23
fxlab market-backfill --from 2023-09-23 --to 2026-09-23
fxlab market-replay data/reports/bars.json
fxlab market-check
fxlab macro-fetch --from 2023-09-01 --to 2026-09-24
fxlab macro-replay data/reports/bls_fetch.json
fxlab cftc-backfill --from 2023-09-01 --to 2026-09-24
fxlab cftc-replay data/reports/cftc_fetch.json
fxlab fomc-backfill --from 2023-09-01 --to 2026-09-24
fxlab fomc-replay data/reports/fomc_fetch.json
fxlab ecb-policy-backfill --from 2023-09-01 --to 2026-09-24
fxlab ecb-policy-replay data/reports/ecb_policy_fetch.json
fxlab align-events
fxlab baseline-experiments
fxlab graph-build
fxlab interaction-experiments
fxlab open-data-backfill --from 2004-09-06
fxlab open-data-backfill --from 2004-09-06 --offline
fxlab denn-baseline
fxlab denn-spectral
fxlab denn-spectral-stability
fxlab denn-timing-audit
fxlab denn-state-vector
fxlab denn-grouped
fxlab denn-grouped-tier-a
fxlab denn-memory-ablation
fxlab denn-block-pca
fxlab denn-elastic-net
fxlab denn-nonlinear
fxlab denn-market-confirmation
fxlab eia-backfill --from 2004-01-01
fxlab cftc-backfill --from 2006-06-13
fxlab tier-a-fetch
fxlab tier-a-features
uvicorn fxlab.web:app --host 127.0.0.1 --port 8088
pytest -q
```

[Веб-журнал](http://192.168.88.15:8088) · [Tier A world-state](http://192.168.88.15:8088/reports/tier-a) · [Memory ablation](http://192.168.88.15:8088/reports/denn-memory-ablation) · [Timing audit](http://192.168.88.15:8088/reports/denn-timing-audit) · [State-vector suite](http://192.168.88.15:8088/reports/denn-state-vector) · [Grouped suite](http://192.168.88.15:8088/reports/denn-grouped) · [План AnyJev](http://192.168.88.15:8088/reports/anyjev) · [Подробный roadmap](http://192.168.88.15:8088/reports/roadmap) · [Spectral stability](http://192.168.88.15:8088/reports/denn-spectral-stability) · [Data Coverage Matrix](http://192.168.88.15:8088/reports/data-coverage)

137 737 валидных H1 с сентября 2004 по сентябрь 2026 года; 5 744 дневные сессии, из них 5 732 полные. В истории найден 21 отсутствующий час. 13 некорректных свечей поставщика исключены, дубликатов нет. Пропуски не заполняются. Повторная сборка из сохранённых исходников дала тот же результат и ту же контрольную сумму.

Open-data matrix содержит 11 continuous D1 series и два event datasets. Для spectral bootstrap общий период по ECB EUR/USD reference, US/EA 2Y/10Y, Brent, WTI и VIX составляет 5 217 дат с 2004-09-07 по 2026-09-22. Dukascopy H1/NY17 D1 теперь покрывает тот же длинный период и станет основной независимой проверкой результатов, ранее рассчитанных на справочном курсе ECB.

Spectral stability использует 18 fixed rolling и 18 expanding окон. Full-sample monthly coherence для 2Y spread не является устойчиво доминирующей в rolling slices: максимальная modal-band share равна 44.4%. Зарегистрированная lag +1 корреляция spread changes с будущим EUR/USD сохраняет отрицательный знак во всех rolling windows, но окна перекрываются на 75%, inputs non-strict, а корреляция не является причинным эффектом.

M4 даёт 267 выровненных строк. M5 использует 153 pre-event строки BLS/FOMC/ECB только для описательного zero-mean baseline. M6 описывает переходы event → surprise → expectations → rates → FX. M7 добавляет 150 trend-regime и 31 positioning-regime feature row, но не называет их surprise-interactions. Строгих PIT-строк пока 0; нулевой результат допустим.

`fxlab open-data-backfill` считает источник интегрированным только после фактической загрузки raw snapshot, нормализации и сохранения локального Parquet. Отчёт показывает календарное покрытие и фактический inner-overlap обязательных рядов.

`fxlab tier-a-fetch` независимо загружает и нормализует 14 world-state рядов. `fxlab tier-a-features` строит nullable as-of матрицу с отдельным common interval для каждого economic block; pre-release и unavailable значения не имитируются.

`fxlab denn-grouped-tier-a` проверяет 16 признаков в 7 economic blocks на 4 639 complete-case D1 строках. Первый зарегистрированный прогон сохранил NULL после Bonferroni: лучший near-miss — rates на 1d (12/14 folds, p=0.01294 при пороге 0.00714); полная ridge-модель не превзошла historical mean ни на одном горизонте.

`fxlab denn-memory-ablation` сравнивает эту же observed-state baseline с 13 заранее заданными EMA-memory и freshness-decay признаками. На 4 639 строках и 14 annual folds memory-вариант не улучшил ни один горизонт: skill vs mean равен −3.54%/−13.20%/−57.41%/−160.59% против baseline −0.68%/−3.41%/−14.22%/−21.44%. Все четыре формальных теста — NULL после Bonferroni 0.0125; отсутствующие значения не заполнялись.

`fxlab denn-block-pca` строит по одному first principal component на каждый из семи economic blocks. Means, scales и loadings оцениваются заново только на прошлых строках каждого walk-forward fold; полный 16-feature ridge остаётся парным control.

Первый зарегистрированный прогон дал directional, но не формальный результат: block states улучшили skill vs mean с −0.68%/−3.41%/−14.22%/−21.44% до +1.14%/−0.16%/−3.49%/+0.22%. Folds в пользу compression = 7/14, 9/14, 10/14 и 9/14; ни один p-value не прошёл Bonferroni 0.0125.

`fxlab denn-elastic-net` использует те же 16 показателей и те же проверочные годы, но может уменьшать влияние слабых рядов до нуля. В отчёте видно, стала ли ошибка меньше, в скольких годах улучшение повторилось и какие показатели модель оставляла чаще всего.

`fxlab denn-nonlinear` сравнивает автоматический отбор с моделью, которая может находить изгибы и сочетания между теми же 16 показателями. Настройки выбираются только на прошлом году, а следующий год остаётся неизвестным обеим моделям.

Результат v0.23: сложные сочетания ухудшили прогноз на всех четырёх сроках и выигрывали только в 3/14, 2/14, 3/14 и 4/14 проверочных лет. Дальнейшее усложнение моделей на этой таблице остановлено; приоритет перенесён на длинный рыночный EUR/USD и CFTC/EIA.

Результат v0.25: дневные изменения ECB и Dukascopy совпадают по направлению в 66.3% дней, их корреляция 0.511. Для связи «изменение разницы ставок 2Y сегодня → EUR/USD завтра» значение изменилось с −0.198 на ECB до −0.009 на Dukascopy. Заранее заданная проверка не пройдена. Одновременная связь внутри дня остаётся заметной (−0.252), но она не является прогнозом будущего движения.

Результат v0.26: на 4 629 строках за 2007–2026 годы самым точным на всех четырёх сроках оказался простой вариант «курс не изменится». Модель со всеми 16 показателями проиграла среднему прошлых лет на 1.44%, 3.74%, 13.20% и 20.85%; автоматический отбор — на 0.20%, 1.22%, 6.26% и 12.82%. Это означает, что следующий прирост качества следует искать в новых данных и точном времени их появления, а не в усложнении модели.

Результат v0.27: восемь признаков CFTC/EIA напрямую ухудшили исходную модель. Автоматический отбор уменьшил её ошибку на 1.28%, 2.02%, 6.43% и 2.32%, однако всё равно проиграл среднему прошлых лет на 0.16%, 1.70%, 5.88% и 18.77%. Для 1 020 старых строк CFTC используется явно отмеченная десятидневная задержка; только 39 последних недель имеют сохранённый календарь публикаций.

Первый прогон улучшил качество относительно обычной 16-feature модели на всех сроках. Результат относительно простого среднего: +0.70%/−0.15%/−6.37%/−13.40% для 1d/5d/20d/60d. Улучшение повторилось в 8/14, 8/14, 11/14 и 12/14 проверочных лет. Чаще всего сохранялись reverse repo ФРС, WTI, разница двухлетних ставок и NFCI; это направление для следующего теста, а не доказанная причина движения EUR/USD.

`fxlab denn-baseline` работает офлайн. Snapshot schema хранит `event_time`, nullable `published_at`/`available_at`/`revision_id`, фактический `ingested_at`, source, unit, source snapshot и quality. Пока исторические vintages не доказаны, все строки имеют `strict_pit_eligible=false`, а отчёт является честным non-strict benchmark, не торговой стратегией.

Первый реальный baseline содержит 41 736 snapshots, 5 098 feature rows, 68 годовых folds и 14 548 out-of-sample predictions. Ridge с шестью factors не превзошёл historical-mean baseline: aggregate skill равен −0.01%, −1.47%, −5.80% и −0.80% для 1d/5d/20d/60d. Это зафиксированный null result и контроль для следующего spectral этапа.

Spectral baseline использует 5 216 aligned changes и 39 перекрывающихся Welch windows по 256 sessions. Самая высокая full-sample mean coherence — 0.245 для изменения US–EA 2Y spread в полосе 30–90 sessions, однако phase указывает, что EUR/USD опережает spread примерно на 41 session. В прямом lag scan сильнейшая связь этого spread с EUR/USD равна −0.198 при factor lead +1 session. Эти значения не имеют causal или significance статуса до rolling stability и multiple-testing checks.

Старый `fxlab acquisition-audit` сохранён как первый CPI/NFP event-study prototype с tick/M1 проверкой. Его vendor-часть не определяет приоритет проекта.

См. DECISIONS.md, OPEN_QUESTIONS.md, DEPLOYMENT.md. `docs/SPEC_RU.md` — исходная спецификация, `docs/DENN_SPEC_RU.md` — текущая research architecture, `docs/ANYJEV_INTEGRATION_RU.md` — проверенная схема AnyJev/Qwen; решения пользователя имеют приоритет. Существующие сайты Caddy не изменяются.
