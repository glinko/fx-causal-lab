# FX Causal Lab

Исследовательский MVP EUR/USD на Ubuntu: публичные источники, provenance, проверки доступности и веб-отчёты.

Версия 0.12.0: acquisition review проверяет первый data investment. Публичный Dukascopy tick-файл декодирован в bid/ask M1 для узкого окна; historical consensus сравнивается по единому контракту приёмки без покупки или искусственного заполнения.

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
uvicorn fxlab.web:app --host 127.0.0.1 --port 8088
pytest -q
```

[Веб-журнал](http://192.168.88.5:8088) · [Interaction experiments](http://192.168.88.5:8088/reports/interaction-experiments) · [Карта гипотез](http://192.168.88.5:8088/reports/causal-graph) · [Baseline experiments](http://192.168.88.5:8088/reports/baseline-experiments) · [События и targets](http://192.168.88.5:8088/reports/event-alignment) · [Качество истории](http://192.168.88.5:8088/reports/market-quality) · [Архив BLS](http://192.168.88.5:8088/reports/macro-data) · [Решения FOMC и ECB](http://192.168.88.5:8088/reports/policy-events) · [Позиционирование CFTC](http://192.168.88.5:8088/reports/positioning)

18 667 валидных H1; 780 дневных сессий, из них 772 полные. 13 некорректных OHLC исключены. Пропуски не заполняются. H1 и D1 доступны в Parquet вместе с манифестом исходных снимков. Модель хранит отдельные timestamps, provenance и nullable consensus/vintages; неизвестная историческая доступность не допускается в строгие эксперименты.

M4 даёт 267 выровненных строк. M5 использует 153 pre-event строки BLS/FOMC/ECB только для описательного zero-mean baseline. M6 описывает переходы event → surprise → expectations → rates → FX. M7 добавляет 150 trend-regime и 31 positioning-regime feature row, но не называет их surprise-interactions. Строгих PIT-строк пока 0; нулевой результат допустим.

`fxlab review-readiness` объединяет manifests M0–M7 в воспроизводимый decision gate. Рейтинг data investments измеряет только покрытие уже описанных гипотез; цена, лицензирование и качество поставщика требуют отдельной проверки.

`fxlab acquisition-audit` сохраняет снимки официальной документации Trading Economics, Econoday и Dukascopy, декодирует контрольный EUR/USD tick-day и публикует M1 sample. `--offline` воспроизводит результат по сохранённым снимкам без сети.

См. DECISIONS.md, OPEN_QUESTIONS.md, DEPLOYMENT.md. docs/SPEC_RU.md — исходная спецификация; решения пользователя имеют приоритет. Существующие сайты Caddy не изменяются.
