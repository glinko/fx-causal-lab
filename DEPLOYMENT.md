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
