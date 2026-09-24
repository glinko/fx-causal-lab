# Market decoder fixture

`dukascopy_h1_2024_01.json` is a preserved public EUR/USD Bid H1 response from:
https://jetta.dukascopy.com/v1/candles/hour/EUR-USD/BID/2024/1

Retrieved 2026-09-23/24 for parser validation. It is a current provider snapshot, not a historical vintage. Tests verify delta reconstruction and absence of synthetic gap filling. Production snapshots carry independent hashes and ingestion receipts under the server data/bronze directory.
