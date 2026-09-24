# FX Causal Lab

Исследовательский MVP EUR/USD на Ubuntu: публичные источники, provenance, проверки доступности и веб-отчёты.

Версия 0.2.0: ECB reference, Dukascopy Bid H1 и дневные NY17 сессии, карантин, отчёт качества и offline replay. Строгий PIT dataset и статистические эксперименты пока не готовы.

```bash
pip install -c requirements.lock '.[test]'
fxlab backfill --from 2023-09-23 --to 2026-09-23
fxlab market-backfill --from 2023-09-23 --to 2026-09-23
fxlab market-replay data/reports/bars.json
fxlab market-check
uvicorn fxlab.web:app --host 127.0.0.1 --port 8088
pytest -q
```

[Веб-журнал](http://192.168.88.5:8088) · [Качество истории](http://192.168.88.5:8088/reports/market-quality)

18 667 валидных H1; 780 дневных сессий, из них 772 полные. 13 некорректных OHLC исключены. Пропуски не заполняются. H1 и D1 доступны в Parquet вместе с манифестом исходных снимков. Модель хранит отдельные timestamps, provenance и nullable consensus/vintages; неизвестная историческая доступность не допускается в строгие эксперименты.

M0 продолжается: BLS, FRED, Eurostat и CFTC проверены на реальных исторических ответах без ключей. Полная матрица — SOURCE_MATRIX.md. Далее M3: календари публикаций, оригинальные releases и revisions; затем M4: event alignment и targets 1/5/20/60 торговых дней. M5 начинается с воспроизведения опубликованных эффектов; отсутствие сигнала допустимо.

См. DECISIONS.md, OPEN_QUESTIONS.md, DEPLOYMENT.md. docs/SPEC_RU.md — исходная спецификация; решения пользователя имеют приоритет. Существующие сайты Caddy не изменяются.
