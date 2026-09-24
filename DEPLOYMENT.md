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
