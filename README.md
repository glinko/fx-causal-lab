# FX Causal Lab

Исследовательский MVP EUR/USD на Ubuntu: публичные источники, provenance, проверки доступности и веб-отчёты.

Версия 0.4.0: ECB reference, Dukascopy Bid H1/D1, 70 архивных релизов BLS и 159 недельных отчётов CFTC TFF по EUR futures. Строгий PIT dataset и статистические эксперименты пока не готовы.

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
uvicorn fxlab.web:app --host 127.0.0.1 --port 8088
pytest -q
```

[Веб-журнал](http://192.168.88.5:8088) · [Качество истории](http://192.168.88.5:8088/reports/market-quality) · [Архив BLS](http://192.168.88.5:8088/reports/macro-data) · [Позиционирование CFTC](http://192.168.88.5:8088/reports/positioning)

18 667 валидных H1; 780 дневных сессий, из них 772 полные. 13 некорректных OHLC исключены. Пропуски не заполняются. H1 и D1 доступны в Parquet вместе с манифестом исходных снимков. Модель хранит отдельные timestamps, provenance и nullable consensus/vintages; неизвестная историческая доступность не допускается в строгие эксперименты.

M0 продолжается: BLS, FRED, Eurostat и CFTC проверены без ключей. В M3 сохранены архивные BLS pages и точные embargo timestamps; `available_at` сознательно оставлен пустым. Для CFTC сохранены годовые TFF archives и текущее расписание: только 38 строк получили консервативную доступность после дня плановой публикации, 121 строка остаётся с неизвестной доступностью. Далее: revisions и Fed/ECB release calendars, затем M4 event alignment и targets 1/5/20/60 торговых дней. M5 начинается с воспроизведения опубликованных эффектов; отсутствие сигнала допустимо.

См. DECISIONS.md, OPEN_QUESTIONS.md, DEPLOYMENT.md. docs/SPEC_RU.md — исходная спецификация; решения пользователя имеют приоритет. Существующие сайты Caddy не изменяются.
