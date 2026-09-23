# Deployment verification — 2026-09-23

- Host: websrv, 192.168.88.5; `/opt/fx-causal-lab`.
- Docker Compose service healthy; Docker enabled at boot; Caddy remains active and unchanged by this deployment.
- `pip check`: no broken requirements.
- Python tests: 8 passed. Covers historical vintage selection, unknown availability, forecast vintage, reaction overlap, raw snapshots, offline replay, duplicates, web empty and loaded states.
- Backfill: 765 reference-rate observations, 2023-09-25 through 2026-09-23. Requested interval began on Saturday 2023-09-23.
- Repeated backfill before/after DuckDB-only conversion produced the same normalized SHA-256: `012854c9465b8a345265de0b15b4540abeb3c36f4ffc76bae6be8acb9fecc10d`.
- Browser: 1440px desktop / 390px mobile; chart filter changes 765 observations to 65 for the final 90 days. All four report routes returned 200. Accessible latest-values table has 10 rows. No JavaScript errors, no page-level mobile overflow.
- Screenshots: local `data/qa-final/desktop.png` and `mobile.png`.
- Independent Impeccable finish verdict: `ship`, after mobile chart reflow and self-hosted Cyrillic typography fixes.
- One non-blocking TestClient deprecation warning remains; production does not use TestClient.

Not validated: full M0 availability/depth, OHLC data provider, full PIT macro dataset, actual COT release alignment, feature store, statistical replication, trading performance. No experiments or live trading enabled. No recurring ingestion configured.
