# Первые проверки источников с websrv

Это результаты начального сетевого и форматного обследования, не сертификат полноты данных. Доказательства ответов сохранены в `data/bronze/recon_*`, машиночитаемый журнал — `data/reports/sources.json`.

- ECB: исторический XML успешно разобран; дневной USD/EUR reference series загружен отдельно в Silver.
- BLS: JSON sample CPI за 2024 год содержит `REQUEST_SUCCEEDED` и наблюдения. Глубина и исходные vintage ещё не проверены.
- CFTC: годовой TFF архив за 2025 год доступен и является ZIP. Полная проверка полей и release calendar впереди.
- Federal Reserve, FRED web, Census page, DOL, DG ECFIN: URL ответили HTTP 200. Это не доказательство готовности исторического dataset.
- ALFRED: API без ключа отвечает HTTP 400. BEA возвращает ответ на неавторизованный API запрос; HTTP 200 не означает успех данных. EIA без ключа отвечает HTTP 403.
- Eurostat: первоначальный HICP-запрос за 2026 год ответил HTTP 200, но не дал непустого `value`. Требуется проверить актуальные codes/classification и доступность выбранной комбинации; источник не объявляется недоступным целиком.
- Destatis: проверенный GET logincheck ответил HTTP 405. Нужно проверить контракт метода, а не делать вывод, что требуется платный доступ.
- Trading Economics: документация PIT доступна; подписка и entitled history не проверены.
- MT5: это optional Windows connector. Наличие документации не означает наличие терминала или брокерской истории.

Точная глубина истории, quotas, стоимость коммерческих plans, исторические release times и revisions остаются открытыми пунктами M0.

## Дополнение 2026-09-24: consensus и narrow-window FX

- Dukascopy official tick format и реальный файл за 2024-01-11 проверены: 126811 ticks, 1437 M1 bars, 61 минутная свеча в контрольном окне ±30 минут вокруг 13:30 UTC. Это transport validation, а не доказательство immutable market vintage.
- Trading Economics документирует PIT calendar и поля Forecast/Previous/Revised/LastUpdate. Публичный guest API больше не даёт sample (HTTP 410); нужен trial export и подтверждение entitlement.
- Econoday публично заявляет архивы с 2001 года, consensus, actual-as-released и revisions. Нужны sample, API contract, лицензия и quote.
- LSEG/Reuters Polls и Bloomberg подтверждают наличие институционального consensus, но отложены как enterprise alternatives.
