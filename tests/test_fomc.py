import hashlib
from datetime import date

import pytest

from fxlab.fomc import normalize_statement, rate_value, release_timestamp, statement_links, target_range


CALENDAR = b'''Statement: <a href="/newsevents/pressreleases/monetary20231101a.htm">HTML</a>
<a href="/newsevents/pressreleases/monetary20231101a.pdf">PDF</a>
<a href="/newsevents/pressreleases/monetary20240918a.htm?x=1">HTML</a>
<a href="/newsevents/pressreleases/monetary20250822a.htm">Statement on Longer-Run Goals</a>'''
HOLD = b'''<html><p class="article__time">November 01, 2023</p><p class="releaseTime">For release at 2:00 p.m. EDT</p>
<p>The Committee decided to maintain the target range for the federal funds rate at 5-1/4 to 5-1/2 percent.</p></html>'''
CUT = """September 18, 2024 For release at 2:00 p.m. EDT. The Committee decided to lower the target range for the federal funds rate by 1/2 percentage point, to 4-3/4 to 5 percent."""
UNICODE_CUT = "The Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 4 to 4‑1/4 percent."


def meta(body):
    return {"sha256": hashlib.sha256(body).hexdigest(), "source_url": "https://www.federalreserve.gov/test.htm",
            "ingested_at": "2026-09-24T00:00:00+00:00"}


def test_calendar_selects_html_statements_by_date():
    assert statement_links(CALENDAR, date(2023, 9, 1), date(2024, 1, 1)) == [{
        "decision_date": "2023-11-01",
        "url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20231101a.htm",
    }]


def test_release_time_and_fractional_target_range():
    text = HOLD.decode()
    assert release_timestamp(text).isoformat() == "2023-11-01T14:00:00-04:00"
    assert target_range(text) == (5.25, 5.5)
    assert target_range(CUT) == (4.75, 5.0)
    assert target_range(UNICODE_CUT) == (4.0, 4.25)
    assert rate_value("5-1/4") == 5.25 and rate_value("5") == 5


def test_statement_keeps_availability_unknown():
    link = statement_links(CALENDAR, date(2023, 1, 1), date(2023, 12, 31))[0]
    row = normalize_statement(HOLD, link, meta(HOLD))
    assert row["target_midpoint"] == 5.375
    assert row["published_time_quality"] == "exact_timestamp"
    assert row["available_at"] is None and row["time_quality"] == "unknown"
    assert row["consensus"] is None and row["surprise"] is None


def test_timezone_mismatch_and_checksum_fail():
    bad_time = HOLD.replace(b"EDT", b"EST")
    with pytest.raises(ValueError, match="timezone"):
        release_timestamp(bad_time.decode())
    link = statement_links(CALENDAR, date(2023, 1, 1), date(2023, 12, 31))[0]
    with pytest.raises(ValueError, match="checksum"):
        normalize_statement(HOLD + b"x", link, meta(HOLD))
