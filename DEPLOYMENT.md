# Развёртывание

Сервер: `websrv` / Ubuntu 24.04 / `192.168.88.5`.

Адрес: [FX Causal Lab](http://192.168.88.5:8088).

Проект: `/opt/fx-causal-lab`. Все команды ниже выполняются в этом каталоге под `alex`.

```bash
sudo docker compose ps
sudo docker compose logs --tail=100 web
curl -fsS http://192.168.88.5:8088/healthz
sudo docker compose exec web fxlab --help
sudo docker compose exec web fxlab recon
sudo docker compose exec web fxlab backfill --from 2023-09-23 --to 2026-09-23
sudo docker compose exec web python -m pytest -q -p no:cacheprovider
```

Повторная нормализация без сети:

```bash
sudo docker compose exec web fxlab replay data/bronze/ecb/EXACT_RECEIPT_FILENAME.json
```

Имя квитанции берётся из `data/bronze/ecb/`; hash payload проверяется перед нормализацией.

## Файлы данных

- `data/bronze/`: исходные ответы и квитанции загрузок.
- `data/silver/ecb_eurusd.parquet`: дневной reference series, не торговые свечи.
- `data/gold/`: резерв для проверенных features/labels; пока пуст.
- `data/reports/`: отчёт о загрузке, журнал источников.

## Обновление и остановка

```bash
sudo docker compose build
sudo docker compose up -d
sudo docker compose stop
```

`stop` останавливает только этот проект. Не использовать системные prune-команды. При обновлении сохранять `.env` и `data/`. Для отката сохранять предыдущий image/tag и предыдущую копию исходников до сборки новой версии.

Docker и Compose установлены из репозиториев Ubuntu. Системные сервисы Docker/containerd включены; существующий Caddyfile не изменён. Привязка порта задаётся `.env`: `FXLAB_BIND=192.168.88.5`. Для другого сервера изменить значение. Не публиковать сервис наружу без аутентификации.

Контейнер read-only, uid 1000; записывать разрешено в data и временный tmpfs. После перезагрузки контейнер поднимается через `restart: unless-stopped`. Загрузки запускаются вручную; обещания ежедневного автоматического обновления нет.

## H1/D1 — версия 0.2

```bash
sudo docker compose exec web fxlab market-backfill --from 2023-09-23 --to 2026-09-23
sudo docker compose exec web fxlab market-replay data/reports/bars.json
sudo docker compose exec web fxlab market-check
sudo docker compose exec web python tools/audit_sources.py
```

`market-replay` использует точные snapshots из манифеста, проверяет их hash и hash нормализованных H1. Не требует сети. `--offline` у backfill использует текущий cache; для точного повтора конкретного запуска нужен именно `market-replay`.

H1/D1, пропуски и карантин: `data/silver/dukascopy/<dataset_id>/`. Манифест `data/reports/bars.json` указывает текущий dataset. D1 — агрегация NY17, а не независимый источник дневных цен. Загрузка с ошибкой контрольной суммы не заменяет текущий манифест. Snapshot календаря закреплён вместе с ценами.

Аудит источников расходует публичные квоты; не запускать его циклически. Результат `data/reports/source_audit.json` содержит фактически измеренную глубину, сырые снимки и ограничения PIT.

## BLS через Windows-коннектор

Сервер получает HTTP 403 от архивных страниц BLS. На Windows выполнить с `PYTHONPATH=src` и `FXLAB_DATA=data`:

```powershell
python -m fxlab macro-fetch --from 2023-09-01 --to 2026-09-24
```

Перенести `data/bronze/bls_*` и `data/reports/bls_fetch.json` на сервер, затем:

```bash
sudo docker compose exec web fxlab macro-replay data/reports/bls_fetch.json
```

Replay не обращается к сети, проверяет SHA-256 каждого raw payload и публикует новый dataset только после успешного разбора всех релизов.

## CFTC TFF positioning — версия 0.4

```bash
sudo docker compose exec web fxlab cftc-backfill --from 2023-09-01 --to 2026-09-24
sudo docker compose exec web fxlab cftc-replay data/reports/cftc_fetch.json
```

`cftc-backfill` сохраняет HTML расписания и годовые Futures Only ZIP, затем создаёт EUR contract `099741` Parquet. `cftc-replay` работает по закреплённым snapshots и проверяет их SHA-256. Для доказательства автономности replay можно запустить через `docker run --network none` с подключённым каталогом `data`.

## FOMC statements — версия 0.5

```bash
sudo docker compose exec web fxlab fomc-backfill --from 2023-09-01 --to 2026-09-24
sudo docker compose exec web fxlab fomc-replay data/reports/fomc_fetch.json
```

Backfill сохраняет FOMC calendar и каждую statement page. Replay проверяет hashes, время релиза, дату URL и target range до публикации нового Parquet. Для точного автономного повтора используется `docker run --network none` с bind mount каталога `data`.

## ECB monetary policy decisions — версия 0.6

```bash
sudo docker compose exec web fxlab ecb-policy-backfill --from 2023-09-01 --to 2026-09-24
sudo docker compose exec web fxlab ecb-policy-replay data/reports/ecb_policy_fetch.json
```

Backfill сохраняет FOEDB versions descriptor, metadata, необходимые chunks и каждую страницу решения. Replay проверяет SHA-256, дату URL, время 14:15 Europe/Berlin и три policy rates. Для доказательства автономности replay запускается с `docker run --network none` и bind mount каталога `data`.

## Event alignment — версия 0.7

```bash
sudo docker compose exec web fxlab align-events
```

Команда не обращается к сети. Она читает текущие манифесты BLS, FOMC, ECB, CFTC и EUR/USD, проверяет anti-leakage invariants и публикует `gold/event_targets/<dataset_id>/event_targets.parquet`.

## Baseline experiments — версия 0.8

```bash
sudo docker compose exec web fxlab baseline-experiments
```

Команда офлайн читает текущий event-alignment manifest, строит 28 описательных event × horizon results и публикует `gold/baseline_experiments/<dataset_id>/results.parquet`. Published-effect registry хранится в manifest вместе с required data и причиной unavailable.

## Causal hypothesis graph — версия 0.9

```bash
sudo docker compose exec web fxlab graph-build
```

Команда не обращается к сети. Она проверяет YAML schema и definition, запрещает циклы и некорректные ссылки, затем публикует JSON и GraphML в `data/graph/<dataset_id>/`. Временные ряды в граф не копируются. Текущий report доступен на `/reports/causal-graph`, exports — через `/download/causal_graph.json` и `/download/causal_graph.graphml`.

## Interaction experiments — версия 0.10

```bash
sudo docker compose exec web fxlab interaction-experiments
```

Команда работает офлайн поверх текущих M4 targets, CFTC и D1 manifests. Она публикует point-in-time joined feature rows и описательные regime summaries в `data/gold/interaction_experiments/<dataset_id>/`. Report доступен на `/reports/interaction-experiments`; feature и result Parquet отдаются отдельными downloads. Команда не рассчитывает surprise из Actual и не делает inferential claims.

## M0–M7 readiness review — версия 0.11

```bash
sudo docker compose exec web fxlab review-readiness
```

Команда офлайн читает текущие manifests M0–M7, фиксирует milestone status, research gates, post-MVP platform decision и ранжированную очередь data investments. Report доступен на `/reports/readiness-review`, машиночитаемый manifest — `/download/readiness_review.json`.

## Consensus + narrow-window FX audit — версия 0.12

```bash
sudo docker compose exec web fxlab acquisition-audit
sudo docker compose exec web fxlab acquisition-audit --offline
```

Первый запуск сохраняет официальные documentation snapshots и один дневной EUR/USD tick-файл Dukascopy. Replay с `--offline` проверяет hashes, декодирует ticks, агрегирует bid/ask M1 и публикует `data/silver/dukascopy_tick_sample/<dataset_id>/eurusd_m1.parquet`. Report доступен на `/reports/acquisition-review`; JSON и M1 Parquet имеют отдельные downloads. Команда не покупает подписку и не включает strict experiments.
