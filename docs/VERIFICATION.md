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

## Version 0.2 verification — 2026-09-23/24

This supersedes the earlier OHLC-provider-unvalidated note above; full historical-vintage/PIT eligibility remains unvalidated.

- 22 tests passed: decoder on real and synthetic fixtures, malformed arrays, quarantine with delta continuity, NY DST/weekends, holidays, missing-hour aggregation, calendar contract changes, pinned raw snapshot hash verification, failed replay retaining current dataset, plus prior PIT/model tests.
- Dependencies: pip check passed. Pytest cache warning comes from the deliberately read-only image; use `-p no:cacheprovider`. TestClient deprecation remains non-blocking.
- Dukascopy: 18,667 valid H1, 780 D1 sessions, 772 complete. 13 invalid OHLC quarantined, 13 missing expected hours, 8 incomplete D1, 0 outside-schedule bars.
- Dataset `7b3115d8f9a9bbec8baf`; exact snapshot replay passed. Manifest pins 38 source snapshots (calendar plus 37 monthly candle responses).
- ECB plausibility comparison: 765 matched days, 0 unmatched, median/p95 distance outside containing H1 range 0 pips. This is not exact-price or historical-availability validation.
- Browser 1440/390: D1, H1, ECB selection; period filter 780 to 65 observations; 10-row accessible tables; five report routes; no JavaScript errors or mobile page overflow, including quality report. Screenshots: data/qa-v02/.
- Service version 0.2.0 healthy on 192.168.88.5:8088. Caddy active.
- Public macro audit: BLS CPI/payrolls 116 monthly observations each, FRED DGS2 2431, Eurostat fixed EA20 HICP 44, CFTC EUR TFF archives 52/53/52/37 for 2023/24/25/26.

Remaining: original macro releases, actual publication times, historical revisions/forecast vintages, broader source-depth/licensing checks, target construction and experiments. No causal result or strategy performance claimed.
- Additional exact replay with Docker `--network none` passed; normalized H1 SHA-256 `af982d05648378355ff388b6468b7b0c0f7a7b56f8e7ac93e1f280ee56b55b5d`.
- Final browser pass: screenshots data/qa-v02-final/, no errors; SVG title and accessible name match the selected description for all three series. Independent finish verdict: ship; no remaining material findings.
