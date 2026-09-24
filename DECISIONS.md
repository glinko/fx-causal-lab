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
