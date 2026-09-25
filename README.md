# FX Causal Lab

Исследовательский MVP EUR/USD на Ubuntu: публичные источники, provenance, проверки доступности и веб-отчёты.

Версия 0.13.0: free/public data coverage становится главным acquisition workflow. Treasury, ECB, EIA, Cboe и EUR/USD загружаются, нормализуются в отдельные Parquet и сводятся в common D1 grid без forward fill. Consensus сохранён как optional enrichment и не блокирует DENN/spectral MVP.

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
uvicorn fxlab.web:app --host 127.0.0.1 --port 8088
pytest -q
```

[Веб-журнал](http://192.168.88.5:8088) · [Data Coverage Matrix](http://192.168.88.5:8088/reports/data-coverage) · [Interaction experiments](http://192.168.88.5:8088/reports/interaction-experiments) · [Карта гипотез](http://192.168.88.5:8088/reports/causal-graph) · [Baseline experiments](http://192.168.88.5:8088/reports/baseline-experiments) · [События и targets](http://192.168.88.5:8088/reports/event-alignment) · [Качество истории](http://192.168.88.5:8088/reports/market-quality) · [Архив BLS](http://192.168.88.5:8088/reports/macro-data) · [Решения FOMC и ECB](http://192.168.88.5:8088/reports/policy-events) · [Позиционирование CFTC](http://192.168.88.5:8088/reports/positioning)

18 667 валидных H1; 780 дневных сессий, из них 772 полные. 13 некорректных OHLC исключены. Пропуски не заполняются. H1 и D1 доступны в Parquet вместе с манифестом исходных снимков. Модель хранит отдельные timestamps, provenance и nullable consensus/vintages; неизвестная историческая доступность не допускается в строгие эксперименты.

Open-data matrix содержит 11 continuous D1 series и два event datasets. Для spectral bootstrap общий inner-overlap по ECB EUR/USD reference, US/EA 2Y/10Y, Brent, WTI и VIX составляет 5 217 дат с 2004-09-07 по 2026-09-22. Dukascopy H1/NY17 D1 остаётся отдельным tradable-price рядом и пока покрывает три года; reference rate не выдаётся за OHLC.

M4 даёт 267 выровненных строк. M5 использует 153 pre-event строки BLS/FOMC/ECB только для описательного zero-mean baseline. M6 описывает переходы event → surprise → expectations → rates → FX. M7 добавляет 150 trend-regime и 31 positioning-regime feature row, но не называет их surprise-interactions. Строгих PIT-строк пока 0; нулевой результат допустим.

`fxlab open-data-backfill` считает источник интегрированным только после фактической загрузки raw snapshot, нормализации и сохранения локального Parquet. Отчёт показывает календарное покрытие и фактический inner-overlap обязательных рядов.

Старый `fxlab acquisition-audit` сохранён как первый CPI/NFP event-study prototype с tick/M1 проверкой. Его vendor-часть не определяет приоритет проекта.

См. DECISIONS.md, OPEN_QUESTIONS.md, DEPLOYMENT.md. `docs/SPEC_RU.md` — исходная спецификация, `docs/DENN_SPEC_RU.md` — текущая research architecture; решения пользователя имеют приоритет. Существующие сайты Caddy не изменяются.
