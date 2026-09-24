from datetime import datetime

import hashlib
import json

import pytest

from fxlab.macro import archive_links, extract_actuals, normalize_release, release_timestamp, replay_bls_releases


INDEX = b'''<a href="/news.release/archives/cpi_07112024.htm">June 2024 Consumer Price Index</a>
<a href="/news.release/archives/cpi_08142024.pdf">July 2024 Consumer Price Index</a>'''
CPI = b'''<html><body>Transmission of material in this release is embargoed until
8:30 a.m. (ET) Thursday, July 11, 2024. THE CONSUMER PRICE INDEX -- JUNE 2024.
The Consumer Price Index for All Urban Consumers (CPI-U) declined 0.1 percent on a seasonally adjusted basis.
The index for all items less food and energy rose 0.1 percent in June.</body></html>'''
NFP = """Transmission of material in this news release is embargoed until USDL-24-1817 8:30 a.m. (ET) Friday, September 6, 2024.
Total nonfarm payroll employment increased by 142,000 in August."""


def meta():
    return {"sha256": "a" * 64, "ingested_at": "2026-09-24T00:00:00+00:00"}


def test_archive_links_only_html_and_reference_period():
    rows = archive_links(INDEX, "cpi")
    assert rows == [{"kind": "cpi", "observation_period": "2024-06",
                     "url": "https://www.bls.gov/news.release/archives/cpi_07112024.htm",
                     "label": "June 2024 Consumer Price Index"}]


def test_release_timestamp_uses_new_york_dst():
    stamp = release_timestamp(CPI.decode())
    assert stamp.isoformat() == "2024-07-11T08:30:00-04:00"
    assert stamp.astimezone().tzinfo is not None


def test_cpi_first_release_actuals_and_unknown_availability():
    link = archive_links(INDEX, "cpi")[0]
    event, rows = normalize_release(CPI, link, meta())
    assert event["published_at"] == datetime.fromisoformat("2024-07-11T08:30:00-04:00")
    assert event["available_at"] is None and event["strict_pit_eligible"] is False
    assert [(row["indicator"], row["actual"]) for row in rows] == [
        ("US_CPI_ALL_MOM_SA", -0.1), ("US_CPI_CORE_MOM_SA", 0.1)]
    assert all(row["consensus"] is None and row["surprise"] is None for row in rows)


def test_nfp_headline_units_and_changed_little():
    assert extract_actuals("empsit", NFP)[0]["actual"] == 142
    small = "Total nonfarm payroll employment changed little (+12,000) in October."
    assert extract_actuals("empsit", small)[0]["actual"] == 12
    assert extract_actuals("empsit", "Total nonfarm payroll employment edged down by 92,000 in February.")[0]["actual"] == -92
    assert extract_actuals("empsit", "Total nonfarm payroll employment was essentially unchanged in October (+12,000).")[0]["actual"] == 12


def test_cpi_older_headline_wording_and_unchanged():
    old = "The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.3 percent in December on a seasonally adjusted basis. The index for all items less food and energy rose 0.2 percent in December."
    flat = "The Consumer Price Index for All Urban Consumers (CPI-U) was unchanged in May on a seasonally adjusted basis."
    assert extract_actuals("cpi", old)[0]["actual"] == 0.3
    assert extract_actuals("cpi", flat)[0]["actual"] == 0
    ambiguous = "The index for all items less food and energy was unchanged in June. The all items less food and energy index rose 2.6 percent over the year."
    assert extract_actuals("cpi", ambiguous)[0]["actual"] == 0


def test_cpi_two_month_shutdown_values_are_not_labeled_monthly():
    shutdown = """The Consumer Price Index for All Urban Consumers (CPI-U) increased 0.2 percent on a seasonally adjusted basis over the 2 months from September 2025 to November 2025.
    The seasonally adjusted index for all items less food and energy rose 0.2 percent over the 2 months ending in November."""
    assert extract_actuals("cpi", shutdown) == [
        {"indicator": "US_CPI_ALL_2M_SA", "actual": 0.2, "unit": "percent_2m"},
        {"indicator": "US_CPI_CORE_2M_SA", "actual": 0.2, "unit": "percent_2m"},
    ]


def test_offline_replay_rejects_changed_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    payload = tmp_path / "release.payload"
    payload.write_bytes(CPI)
    snapshot = {**meta(), "payload": "release.payload", "sha256": hashlib.sha256(CPI).hexdigest(),
                "source_url": "https://www.bls.gov/news.release/archives/cpi_07112024.htm"}
    manifest = {"requested_start": "2024-06-01", "requested_end": "2024-06-30",
                "snapshots": [snapshot], "releases": [{"link": archive_links(INDEX, "cpi")[0], "snapshot": snapshot}]}
    report = replay_bls_releases(manifest)
    assert report["release_rows"] == 1 and report["observation_rows"] == 2
    assert len(report["normalized_sha256"]) == 64 and report["parser"] == "bls-release-html-1"
    payload.write_bytes(CPI + b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        replay_bls_releases(manifest)
