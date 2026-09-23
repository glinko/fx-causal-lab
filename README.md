# FX Causal Lab

Исследовательский MVP EUR/USD. Ubuntu-first, публичные источники, point-in-time проверки и веб-отчёты.

Текущая версия: начальный сервис и ECB reference data slice. **Не торговая система и не завершённая Research Platform v1.**

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
fxlab backfill --from 2023-09-23 --to 2026-09-23
fxlab recon
uvicorn fxlab.web:app --host 127.0.0.1 --port 8088
pytest -q
```

Либо Docker Compose. На сервере [журнал](http://192.168.88.5:8088) показывает реальные загруженные данные и состояние источников.

См. `DECISIONS.md`, `OPEN_QUESTIONS.md`, `SOURCE_MATRIX.md`, `DEPLOYMENT.md`. Исходная спецификация находится в `docs/SPEC_RU.md`; решения пользователя имеют приоритет над ней.

## Что реализовано

- Отдельный веб-сервис, график ECB EUR/USD, выбор периода, данные для скачивания и отчёты.
- Immutable raw payloads, отдельные ingestion receipts, воспроизводимая offline-нормализация.
- Parquet + DuckDB для reference series; без ложных OHLC и PIT claims.
- Контракт MarketProvider, модели provenance/vintages/consensus, as-of selection и anti-leakage window checks.
- Проверка доступности 15 источников с сервера и сохранением доказательств.

## Что дальше

Завершить M0 и выбрать OHLC provider, затем закончить core schemas/коллекторы и H1/D1. Для M5 сначала опубликованные эффекты; для M6 граф гипотез. GraphML, graph UI, макроадаптеры, features и эксперименты пока не реализованы.
