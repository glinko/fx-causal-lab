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

## Version 0.3 BLS archive slice — 2026-09-24

- Windows connector preserved 72 BLS snapshots: two archive indexes and 70 release pages for 2023-09 through 2026-08.
- Offline Ubuntu normalization produced 70 release events and 105 headline observations: 34 all-items CPI MoM SA, 34 core CPI MoM SA, two separate CPI 2M values for the September–November 2025 interval, and 35 NFP changes.
- All raw payload hashes are checked before parsing. Final dataset: `d760b7e38765578bf457`; normalized SHA-256 is recorded in its manifest.
- Twenty-eight Python tests pass, including archive link filtering, New York DST, wording variants, unknown historical availability and corrupted-payload rejection.
- `published_at` comes from the page embargo timestamp. `available_at` is null, consensus is null, strict PIT eligibility is false. No surprise experiment is enabled.
- Final exact replay passed in Docker with `--network none`. The deployed Parquet has 70 unique release IDs and 105 unique observation IDs; all 105 have null `available_at` and consensus.
- Browser verification covers desktop and 390px mobile, all six report routes, D1/H1/ECB switching, horizontal table cues, downloads, accessible chart identities and page overflow. Independent finish verdict: `ship`.

## Version 0.4 CFTC positioning slice — 2026-09-24

- Official annual TFF Futures Only snapshots normalize to 159 unique EUR contract `099741` rows from 2023-09-05 through 2026-09-15.
- Dataset `5fa5ee27e7f1ee4c7bb2`; normalized SHA-256 `6d3e1877c3e4fe764350e1ac1b217081805dcf82efc5110027824c03680a6126`.
- 38 rows are covered by the preserved current release schedule and receive `inferred_conservative` availability at 00:00 New York time after the scheduled release day. 121 rows remain unknown; strict PIT eligibility is false for all rows.
- One official non-Tuesday report date is preserved. No guessed correction, historical actual release timestamp, options position, or roll adjustment is added.
- Exact replay passed in Docker with `--network none`; 32 Python tests and `pip check` passed. The API returns 159 rows: 38 with availability and 121 unknown; no available timestamp is on or before its report date.
- Browser verification covers the CFTC report at 1440px and 390px, reports no JavaScript errors or page-level mobile overflow, and confirms all seven report routes return HTTP 200. Screenshots: `data/qa-v04/`.
- Deployed image `fx-causal-lab:0.4.0` is healthy on `192.168.88.5:8088`; manifest and Parquet downloads return successfully. Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship`; no material hierarchy, accessibility, mobile-scroll, or time-semantics findings.

## Version 0.5 FOMC statements slice — 2026-09-24

- The official calendar yielded 25 target-range statements from 2023-09-20 through 2026-09-16. A special notation-vote strategy statement was excluded by link semantics.
- Every row has a page-level `For release at` timestamp and parsed lower/upper target bounds. There are 17 holds, six cuts and one hike among the 24 rows with an in-window predecessor.
- Dataset `bd5075d93c6feceee6e6`; normalized SHA-256 `7f01c4f0817b6834f28294a9a3c151705d92e6c533c878dac77c097ad3352901`.
- All 25 `available_at` values remain null and strict PIT is disabled. Exact official release time is not represented as proof of historical receipt.

## Version 0.6 ECB policy decisions slice — 2026-09-24

- The official FOEDB index yielded 25 `Monetary policy decisions` releases from 2023-09-14 through 2026-09-10. Every retained timestamp validates as 14:15 Europe/Berlin on the date encoded in its URL.
- Each release page yielded deposit facility, main refinancing operations and marginal lending facility rates. There are 14 holds, eight cuts and two hikes among the 24 rows with an in-window predecessor.
- Dataset `a6778ebecbe4707bc840`; normalized SHA-256 `9d9fd8bfeb646fec45ccf7f89924cef054e8df639c90de415a7a19bc654f7cfe`.
- All 25 `available_at` values remain null and strict PIT is disabled. Versions descriptor, metadata, data chunks and release pages are hash-pinned for offline replay.
- Container verification: 40 tests passed, `pip check` reported no broken requirements, and both FOMC and ECB manifests replayed successfully under `--network none` with stable dataset IDs and normalized hashes.
- Browser verification covers the merged policy report at 1440px and 390px, switches between FOMC and ECB, validates the SVG identity, reports no JavaScript errors or page-level mobile overflow, and confirms all eight report routes return HTTP 200. Screenshots: `data/qa-v06/`.
- Deployed image `fx-causal-lab:0.6.0` is healthy on `192.168.88.5:8088`; both policy APIs return 25 rows, all four manifest/Parquet downloads succeed, and Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship`; no material findings in rate semantics, PIT disclosure, mobile layout or accessibility.

## Version 0.7 M4 event alignment — 2026-09-24

- Gold dataset `152e8e089a0f74aee7a8`; normalized SHA-256 `17e4fc876ae93580cc40e491cd5c09dea76cf12cd4372b3ddd5cd065c42fd4c8`.
- 267 rows: 191 pre-event, 38 post-release and 38 reaction-confirmed. Source rows: BLS 105, FOMC 24, ECB 24 and CFTC 114 across its three modes.
- Target coverage is 265/260/245/206 rows at 1/5/20/60 sessions. Two earliest policy events have no market anchor because EUR/USD history starts later; 121 CFTC rows have no preserved release reference and are excluded.
- Every target starts at or after prediction time. Pre-event actuals are feature-ineligible; post-release actuals require `available_at <= prediction_time`; incomplete sessions are not skipped.
- Strict PIT rows: 0. This is expected while event receipt and market-history vintages remain unverified.
- Container test run: 45 tests passed with one upstream TestClient deprecation warning.
- Offline rerun produced the same dataset ID and normalized hash. Direct Parquet assertions found zero duplicate IDs, targets before prediction time, pre-event Actual features, Actual uses before availability, or endpoints before target start.
- Browser verification covers the alignment report at 1440px and 390px, reports no JavaScript errors or page-level mobile overflow, and confirms all nine report routes return HTTP 200. Screenshots: `data/qa-v07/`.
- Deployed image `fx-causal-lab:0.7.0` is healthy on `192.168.88.5:8088`; Gold manifest and Parquet downloads succeed, and Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship`; no material findings in prediction-mode clarity, PIT disclosure, mobile table behavior or accessibility.

## Version 0.8 M5 descriptive baseline — 2026-09-24

- Dataset `1d9ef1fec37056c48e98`; normalized SHA-256 `ef656ef290716099b3619bfc43253c69b124074f4e766056dba076b77f119385`.
- Input: 153 pre-event BLS/FOMC/ECB rows. Output: seven event groups × four horizons = 28 rows; 20 meet the minimum N=8 diagnostic threshold.
- Newey–West/HAC lags are 0/0/1/3 at 1/5/20/60 sessions. Overlapping windows are counted per result. Benjamini–Hochberg correction is applied across the 20 diagnostic mean-return tests.
- No diagnostic test has BH q < 0.05. No Actual value or Actual direction is used as a pre-event feature. Strict PIT rows remain 0.
- Five published-effect entries are recorded as unavailable with source, required data and blocking reason. Missing consensus or futures/OIS surprises are never synthesized.
- Container test run: 48 tests passed with one upstream TestClient deprecation warning.
- Exact Docker replay with `--network none` reproduced dataset `1d9ef1fec37056c48e98` and normalized SHA-256 `ef656ef290716099b3619bfc43253c69b124074f4e766056dba076b77f119385`.
- Browser verification covers all ten report routes at 1440px and 390px, with no JavaScript errors or page-level mobile overflow. The final baseline table exposes q, overlap and status on desktop and as complete cards on mobile. Screenshots: `data/qa-v08-final/`.
- Deployed image `fx-causal-lab:0.8.0` is healthy on `192.168.88.5:8088`; the baseline page, JSON manifest and Parquet download return HTTP 200. Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship` after making the no-effect caveat and all statistical qualifiers immediately visible.

## Version 0.9 M6 causal hypothesis graph — 2026-09-24

- YAML schema and definition validate as a directed acyclic graph with 18 semantic nodes, 18 edges and seven priority EUR/USD chains. Validation rejects duplicate/unknown references, self-edges, invalid lag intervals and cycles.
- Six nodes map to currently available but non-strict datasets; 12 required nodes are unavailable. Edge evidence status is 7 unavailable and 11 untested. All seven chains remain blocked and strict-ready count is 0.
- Dataset `38633b58c4737e3e2092`; normalized SHA-256 `49136f86413dacb011a39f07b8496871b3a607b3db9492095dd6a410a1f809ca`.
- Two Docker runs with `--network none` reproduced the same dataset ID and normalized hash. JSON and GraphML exports were written; the test suite reads the GraphML back through NetworkX and confirms 18 nodes/18 edges.
- Container verification: 52 tests passed; `pip check` found no broken requirements. The existing upstream TestClient deprecation warning remains non-blocking.
- Browser verification covers all 11 report routes at 1440px and 390px. Chain filtering, keyboard node activation, retained SVG title/description, the native HTML node-control equivalent, downloads and page-level overflow checks pass with no JavaScript errors. Screenshots: `data/qa-v09-final/`.
- Deployed image `fx-causal-lab:0.9.0` is healthy on `192.168.88.5:8088`; report, API, JSON, GraphML and manifest return HTTP 200. Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship` after the SVG accessible-name fix and addition of native HTML node controls.

## Version 0.10 M7 descriptive interactions — 2026-09-24

- Dataset `f317798e36f1d94acc36`; normalized SHA-256 `beb6f739dd9fd41f716af6bd31fac13f35de5ec5d0522d86b203c3be203fbea6`.
- From 153 pre-event BLS/FOMC/ECB rows, the as-of join produced 181 feature rows: 150 trailing-20-session FX-trend regimes and 31 expanding CFTC-positioning regimes. Output contains 22 event/regime groups and 88 horizon summaries.
- Positioning excludes 114 event rows with no available CFTC observation and eight with fewer than eight previously available reports. FX trend excludes three early events with fewer than 21 available complete D1 sessions.
- The 88 summaries contain 16 single-observation rows, 33 additional rows with N<8 and 39 rows with N>=8. All remain descriptive non-strict; no p-values, q-values, formal regime contrasts, causal claims or trading rules are produced.
- Actual, Actual sign and post-release reactions are absent from features. Each feature has `available_at <= prediction_time`; targets retain the M4 boundary. Strict PIT rows remain 0.
- The requirements registry keeps surprise × positioning, surprise × regime, oil × inflation expectations and rate differential × risk sentiment unavailable with their missing inputs and reasons.
- Two Docker executions with `--network none` reproduced the same dataset ID and normalized hash. Feature and result Parquet plus the manifest are downloadable.
- Container verification: 56 tests passed; `pip check` found no broken requirements. The existing upstream TestClient deprecation warning remains non-blocking.
- Browser verification covers all 12 report routes at 1440px and 390px. Filter and pagination changes are asserted, at most 12 result rows are visible, no JavaScript errors or page-level mobile overflow occur. Screenshots: `data/qa-v010-final2/`.
- Deployed image `fx-causal-lab:0.10.0` is healthy on `192.168.88.5:8088`; report, manifest and both Parquet downloads return HTTP 200. Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship`; no material findings in scientific framing, sample warnings, requirements, exclusions, mobile layout or accessibility.

## Version 0.11 M0–M7 readiness review — 2026-09-24

- Dataset `9da3e3a3c91151440e94`; normalized SHA-256 `46da50ad7e22551efbcc9453842168af0f9b795ef5bda39089359e6f3e0f12a0`.
- The review reads all current M0–M7 manifests and records eight milestone assessments. M0 and M3 remain partial; the implemented M1–M7 slices are explicitly distinguished from strict research readiness.
- All four evidence gates remain zero: strict PIT rows, available published-effect replications, strict-ready graph chains and strict interaction rows.
- Research-unlock ranking is historical consensus + narrow-window FX (18), futures/OIS policy surprises (15), comparable US/EA 2Y yields (11), oil/inflation-expectation vintages (7), and historical CFTC availability (4). The score excludes cost, licensing and source quality.
- Decision: defer OpenSPG and GNN/ML, keep the current web scope, and investigate the ranked data packages before adding platform complexity.
- Two Docker executions with `--network none` reproduced the same dataset ID and normalized hash.
- Container verification: 58 tests passed; `pip check` found no broken requirements. The existing upstream TestClient deprecation warning remains non-blocking.
- Browser verification covers all 13 report routes at 1440px and 390px, verifies both decision statements, eight milestones and five priorities, and reports no JavaScript errors or page-level mobile overflow. Screenshots: `data/qa-v011/`.
- Deployed image `fx-causal-lab:0.11.0` is healthy on `192.168.88.5:8088`; report and JSON manifest return HTTP 200. Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship`; all five direction-contract sections passed with no material visual, responsive or accessibility finding.

## Version 0.12 consensus + narrow-window FX acquisition audit — 2026-09-24

- Six source snapshots are preserved for Trading Economics PIT/intraday/pricing documentation, Econoday product documentation, Dukascopy tick-format documentation and the EUR/USD tick sample.
- Dukascopy sample day 2024-01-11 decoded to 126811 ordered bid/ask ticks and 1437 M1 rows. First tick is 00:00:00.148 UTC; last tick is 23:59:57.874 UTC.
- The control window from 13:00 through 14:00 UTC contains all 61 expected minute starts. Median spread is 0.3 pip and empirical P95 is 0.4 pip.
- Dataset `5f48f93cf1bb6f7015dd`; normalized SHA-256 `a1f3f2d1f12286fcd6f40adcf3eeffab7291fa204856ef50db1051b20b0286e2`.
- Online execution and two Docker executions with `--network none` reproduced the same dataset ID and normalized hash.
- Consensus decision remains `not_ready`: Econoday and Trading Economics require the same CPI/NFP sample, license and price validation; LSEG and Bloomberg are deferred enterprise alternatives.
- Container verification: 63 tests passed; `pip check` found no broken requirements. The existing upstream TestClient deprecation warning remains non-blocking.
- Browser verification covers all 14 report routes at 1440px and 390px, verifies both decision statements, four vendor candidates and seven acceptance checks, and reports no JavaScript errors or page-level mobile overflow. Screenshots: `data/qa-v012/`.
- Deployed image `fx-causal-lab:0.12.0` is healthy on `192.168.88.5:8088`; report, JSON manifest and M1 Parquet return HTTP 200. Strict PIT eligibility remains false and Caddy remains active and unchanged.
- Independent Impeccable finish review verdict: `ship`; all five contract sections passed with no material visual, responsive or accessibility finding.

## Version 0.13 free/public data coverage — 2026-09-25

- Real downloads were normalized for U.S. Treasury 2Y/10Y, ECB euro-area AAA 2Y/10Y, EIA Brent/WTI, Cboe VIX, ECB EUR/USD reference and the existing Dukascopy EUR/USD D1 series. US–EA 2Y/10Y spreads are deterministic date intersections.
- Coverage manifest dataset `1d90dc4ce3e596158090`; normalized SHA-256 `1826792a8557a569c4052484d542b3c4eae3c67576cef689a699759c777c1019`.
- The report contains 11 continuous D1 series plus CFTC EUR and BLS event datasets. Ten continuous series meet the configured long-history readiness threshold; Dukascopy remains partial.
- The spectral-bootstrap common grid uses eight continuous inputs and contains 5 217 dates from 2004-09-07 through 2026-09-22. It uses ECB EUR/USD reference for long D1 coverage and keeps Dukascopy H1/NY17 D1 separate.
- No source row or common-grid date is forward-filled. All downloaded current histories remain `strict_pit_eligible=false`; the ECB rate is explicitly not an executable OHLC price.
- Exact replay passed in Docker with `--network none` and reproduced the dataset ID and normalized hash. The server test suite passed 71 tests; the only warning is the existing upstream TestClient deprecation.
- The attempted long Dukascopy H1 expansion preserved 33 additional monthly snapshots before the provider rate limit stopped publication of a replacement dataset. The previous validated three-year market manifest remains current; cached progress is retained for a later respectful resume.
- Consensus procurement is paused and optional. The v0.12 tick/M1 work is retained as the first CPI/NFP event-study prototype.

## Version 0.14 DENN deterministic baseline — 2026-09-25

- Input coverage dataset `1d90dc4ce3e596158090`; DENN dataset `d6368f13122676b3fa55`; normalized SHA-256 `d6368f13122676b3fa55c59894ee3273b85654c5d93dec175b7cf1edac5bf367`.
- The real 2004-09-07 through 2026-09-22 grid produced 41,736 unified node snapshots, 5,098 feature rows, 68 expanding annual folds and 14,548 out-of-sample predictions.
- Snapshot Parquet contains typed `event_time`, `published_at`, `available_at`, `ingested_at`, source, unit, revision ID, source snapshot ID, time quality and strict-PIT eligibility. All `available_at` and revision IDs remain null for current-history inputs; strict-PIT rows remain zero.
- The six frozen features are US–EA 2Y/10Y spread z-scores, 20-session Brent/WTI asinh changes, VIX z-score and EUR/USD momentum. The asinh transform handles the observed negative WTI value without deleting or inventing data.
- Ridge penalty is selected from the preceding calendar year. Training labels are purged by `target_end_date`; each target begins on the common-grid date after the feature boundary.
- Aggregate skill versus the expanding historical-mean forecast is −0.01% (1d), −1.47% (5d), −5.80% (20d) and −0.80% (60d). No horizon beats the mean baseline. This is retained as the required null control, not presented as a trading or causal result.
- Two Docker executions with `--network none` reproduced the same dataset ID, normalized hash and 14,548 prediction rows.
- Container verification: 71 tests passed and `pip check` found no broken requirements. The existing upstream TestClient deprecation warning remains non-blocking.
- Deployed image `fx-causal-lab:0.14.0` is healthy on `192.168.88.5:8088`. The report, JSON manifest and four Parquet downloads return HTTP 200.
- Browser verification confirmed four horizon rows, no JavaScript warnings/errors and no page-level overflow at a 390px viewport (`scrollWidth` 375). Caddy was not changed.

## Version 0.15 DENN spectral baseline — 2026-09-25

- Input coverage dataset `1d90dc4ce3e596158090`, deterministic baseline `d6368f13122676b3fa55`; spectral dataset `2774a65ea59eb03600da`, normalized SHA-256 `2774a65ea59eb03600dab2e1bb22fe2b34ebde1ce293de8f02cc00c1c7eb5501`.
- The fixed preprocessing contract produced 5,216 aligned one-session changes for EUR/USD, US–EA 2Y/10Y spreads, Brent, WTI and VIX. No input is forward-filled.
- Welch diagnostics use 39 Hann windows of 256 common sessions with step 128. Output contains 25 factor×band rows, 36 Haar detail-energy rows and 605 correlations covering five factors × 121 lags.
- A synthetic 16-session signal delayed by four sessions verifies FFT frequency, coherence, phase-derived lead sign and direct lag convention. Negative WTI remains supported through asinh differences.
- A cross-platform config test verifies identical YAML identity under LF and CRLF line endings.
- Highest full-sample mean coherence is 0.245 for 2Y spread changes in the 30–90-session band, with peak coherence 0.358 near 85.3 sessions. Its phase implies EUR/USD leads the spread by about 41 sessions, so it does not confirm the hypothesized rates → FX direction.
- The strongest absolute direct lag correlation is −0.198 for 2Y spread changes at factor lead +1 common session. Other maxima are −0.156 for 10Y spread at +1, 0.100 for Brent at 0, −0.064 for VIX at 0 and 0.046 for WTI at 0.
- Two server executions with `--network none` reproduced the same dataset ID and normalized hash. All results remain `descriptive_non_strict`; no significance, causality or trading claim is made.

## Version 0.16 DENN spectral stability — 2026-09-25

- Input spectral dataset `2774a65ea59eb03600da`; stability dataset `5669af128af9a5fd789f`; normalized SHA-256 `5669af128af9a5fd789f615dae5ab1f3453639b22005b053f0e13b800ba93296`.
- The frozen contract creates 18 rolling windows of 1,024 common sessions with step 256 and 18 expanding windows from 1,024 sessions with step 256. Output contains 900 window×factor×band rows and 720 registered-lag rows for lags 0/1/5/20.
- Two Ubuntu executions with `--network none` reproduced the same dataset ID and normalized hash. The report manifest timestamp is intentionally excluded from content identity.
- No rolling factor has a modal-band share above 44.4%. For 2Y and 10Y spread changes, slow is the most frequent rolling band with 44.4%, while expanding windows favor monthly with 100% and 94.4%; nested expanding windows are not independent observations.
- Registered lag +1 correlations for spread changes remain negative in all 18 overlapping rolling windows: median −0.203 with Q10/Q90 −0.290/−0.137 for 2Y, and median −0.163 with Q10/Q90 −0.240/−0.067 for 10Y.
- Rolling windows overlap by 75%; expanding windows are nested; band widths contain different frequency-bin counts. The result remains `descriptive_non_strict` with no significance, causality, prediction or trading claim.
- Local verification: 74 tests passed with one upstream TestClient deprecation warning. Browser structure exposes 10 band leaders, 10 lag-one summaries, seven explicit limitations and all five artifact downloads.
