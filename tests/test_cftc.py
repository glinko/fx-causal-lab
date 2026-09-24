import csv
import hashlib
import io
import zipfile
from datetime import date

import pytest

from fxlab.cftc import normalize_tff, release_schedule


SCHEDULE = b'''<html><h3>2026 Release Schedule</h3><table>
<tr><th>Month</th><th>Dates</th></tr>
<tr><td>January</td><td>05*</td><td>09</td><td>16</td><td>23</td><td>30</td></tr>
<tr><td>February</td><td>06</td><td>13</td><td>20</td><td>27</td></tr>
<tr><td>March</td><td>06</td><td>13</td><td>20</td><td>27</td></tr>
<tr><td>April</td><td>03</td><td>10</td><td>17</td><td>24</td></tr>
<tr><td>May</td><td>01</td><td>08</td><td>15</td><td>22</td><td>29</td></tr>
<tr><td>June</td><td>05</td><td>12</td><td>22*</td><td>26</td></tr>
<tr><td>July</td><td>06*</td><td>10</td><td>17</td><td>24</td><td>31</td></tr>
<tr><td>August</td><td>07</td><td>14</td><td>21</td><td>28</td></tr>
<tr><td>September</td><td>04</td><td>11</td><td>18</td><td>25</td></tr>
<tr><td>October</td><td>02</td><td>09</td><td>16</td><td>23</td><td>30</td></tr>
<tr><td>November</td><td>06</td><td>16*</td><td>20</td><td>30*</td></tr>
<tr><td>December</td><td>04</td><td>11</td><td>18</td><td>28*</td></tr></table></html>'''


def fixture_zip(report_date="2026-01-06", negative=False):
    fields = {"Market_and_Exchange_Names": "EURO FX - CHICAGO MERCANTILE EXCHANGE",
              "Report_Date_as_YYYY-MM-DD": report_date, "CFTC_Contract_Market_Code": "099741",
              "FutOnly_or_Combined": "FutOnly", "Open_Interest_All": "1000",
              "NonRept_Positions_Long_All": "100", "NonRept_Positions_Short_All": "80"}
    for prefix in ("Dealer", "Asset_Mgr", "Lev_Money", "Other_Rept"):
        fields[f"{prefix}_Positions_Long_All"] = "-1" if negative and prefix == "Dealer" else "100"
        fields[f"{prefix}_Positions_Short_All"] = "80"
        fields[f"{prefix}_Positions_Spread_All"] = "10"
    target = io.StringIO()
    writer = csv.DictWriter(target, fieldnames=fields)
    writer.writeheader(); writer.writerow(fields)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("FinFutYY.txt", target.getvalue())
    return output.getvalue()


def meta(body):
    return {"sha256": hashlib.sha256(body).hexdigest(), "source_url": "https://example.test/cftc.zip",
            "ingested_at": "2026-09-24T00:00:00+00:00"}


def test_schedule_parses_holiday_dates():
    dates = release_schedule(SCHEDULE)
    assert date(2026, 1, 5) in dates and date(2026, 6, 22) in dates and len(dates) == 52


def test_tuesday_positions_wait_for_scheduled_release():
    body = fixture_zip()
    row = normalize_tff(body, meta(body), date(2026, 1, 1), date(2026, 1, 31), release_schedule(SCHEDULE))[0]
    assert row["report_date"] == "2026-01-06"
    assert row["scheduled_release_at"].isoformat() == "2026-01-09T15:30:00-05:00"
    assert row["available_at"].isoformat() == "2026-01-10T00:00:00-05:00"
    assert row["available_at"].date() > date.fromisoformat(row["report_date"])
    assert row["leveraged_funds_net"] == 20 and row["leveraged_funds_net_share_oi"] == .02
    assert row["strict_pit_eligible"] is False


def test_old_rows_have_unknown_availability_and_bad_positions_fail():
    body = fixture_zip("2024-01-02")
    row = normalize_tff(body, meta(body), date(2024, 1, 1), date(2024, 1, 31), release_schedule(SCHEDULE))[0]
    assert row["available_at"] is None and row["time_quality"] == "unknown"
    bad = fixture_zip(negative=True)
    with pytest.raises(ValueError, match="non-negative"):
        normalize_tff(bad, meta(bad), date(2026, 1, 1), date(2026, 1, 31), release_schedule(SCHEDULE))
