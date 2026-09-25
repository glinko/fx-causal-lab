# FX Causal Lab
<!-- impeccable:product-schema 1 -->

## Platform
web

## Stack
Python 3.12, Docker Compose, DuckDB/Parquet on Ubuntu. Implementation details delegated by the user; simple server-rendered web reports, CLI and notebooks. No external hosting service.

## Users
One researcher investigating EUR/USD over hours to months.

## Product Purpose
Build a reproducible point-in-time research pipeline. A null result is a valid result. Separate measured facts, availability assumptions and causal hypotheses.

## Operating Context
Deploy on websrv, 192.168.88.5. Existing Caddy sites must remain intact. Public data first. No paid credentials required for bootstrap.

## Capabilities and Constraints
Primary targets: 1, 5, 20, 60 trading days. H1 secondary. Separate pre-event, post-release and reaction-confirmed experiments. Unknown historical availability is excluded from strict experiments. Consensus stays null if unavailable. OpenSPG is deferred.
Web interface must show actual data coverage, reports and visualizations. No invented experiment results.

## Evidence on Hand
User specification in docs/SPEC_RU.md. Local legacy forex datasets contain known timestamp and OHLC issues; quarantine rather than silently using them for research.

## Product Principles
Provenance first. No look-ahead. Document assumptions without interrupting routine development. Reproduce published effects before exploratory hypothesis searches.

## Current Research Direction
Free/public historical coverage comes before vendor consensus. Build a common D1 continuous-factor dataset, deterministic DENN snapshot/decay features and a spectral baseline before any Dynamic GNN. Keep the CPI/NFP tick work as an event-study prototype and keep premium sources optional.
