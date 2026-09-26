"""Tier A parser and as-of boundary regression tests."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone

import pytest

from fxlab import tier_a


def test_gsw_parser_finds_real_uppercase_header_and_skips_na():
    lines = ["research note", "Date,BKEVEN05,BKEVEN10,TIPSY05,TIPSY10"]
    start = date(2000, 1, 1)
    for index in range(1001):
        day = start + timedelta(days=index)
        lines.append(f"{day.isoformat()},{1 + index / 1000},{2 + index / 1000},{3 + index / 1000},{4 + index / 1000}")
    parsed = tier_a._parse_gsw("\n".join(lines).encode())
    assert len(parsed["BKEVEN05"]) == 1001
    assert parsed["TIPSY10"][0][1] == pytest.approx(4.0)


def test_nfci_parser_accepts_official_us_date_format():
    lines = ["Friday_of_Week,NFCI,ANFCI"]
    start = date(2000, 1, 7)
    for index in range(1001):
        day = start + timedelta(days=7 * index)
        lines.append(f"{day:%m/%d/%Y},{index / 1000},{-index / 1000}")
    parsed = tier_a._parse_nfci("\n".join(lines).encode())
    assert parsed["NFCI"][0][0] == start
    assert len(parsed["ANFCI"]) == 1001


def test_h41_zip_parser_extracts_ratios_from_sdmx_series():
    start = date(2002, 12, 18)
    mapping = {
        "RESPPLLDT_N.WW": 10.0,
        "RESPPLLR_N.WW": 20.0,
        "RESH4R_N.WW": 30.0,
        "RESPPA_N.WW": 100.0,
    }
    chunks = ["<root>"]
    for series, value in mapping.items():
        chunks.append(f'<Series SERIES_NAME="{series}">')
        for index in range(501):
            day = start + timedelta(days=7 * index)
            chunks.append(f'<Obs TIME_PERIOD="{day}" OBS_VALUE="{value + index}"/>')
        chunks.append("</Series>")
    chunks.append("</root>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("H41_data.xml", "".join(chunks))
    parsed = tier_a._parse_h41(buffer.getvalue())
    assert len(parsed["H41_TGA"]) == 501
    assert parsed["H41_TGA"][0][1] == pytest.approx(0.1)
    assert parsed["H41_RESERVES"][100][1] == pytest.approx(130 / 200)


def test_tic_parser_uses_grand_total_machine_columns():
    lines = [
        "Table 3", "Country\tCountry Code\tDate\tHoldings\tNet U.S. Sales\tHoldings\tNet U.S. Sales",
        "country\tcountry_code\tdate\tfor_treas_pos\tfor_treas_net\tfor_lt_treas_pos\tfor_lt_treas_net",
    ]
    cursor = date(2020, 1, 1)
    for index in range(61):
        year = cursor.year + (cursor.month - 1 + index) // 12
        month = (cursor.month - 1 + index) % 12 + 1
        lines.append(f"Grand Total\t99996\t{year:04d}-{month:02d}\t{7000 + index}\t1\t{6000 + index}\t2")
    parsed = tier_a._parse_tic("\n".join(lines).encode())
    assert len(parsed["TIC_FOREIGN_TREASURY_POS"]) == 61
    assert parsed["TIC_LT_FOREIGN_TREASURY_POS"][0][1] == 6000
    assert parsed["TIC_FOREIGN_TREASURY_POS"][0][0] == date(2020, 1, 31)


def test_yahoo_parser_uses_adjusted_close():
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    stamps = [int((start + timedelta(days=index)).timestamp()) for index in range(1001)]
    body = json.dumps({"chart": {"result": [{"timestamp": stamps, "indicators": {
        "quote": [{"close": [10.0] * 1001}], "adjclose": [{"adjclose": [9.0] * 1001}]
    }}], "error": None}}).encode()
    parsed = tier_a._parse_yahoo(body)
    assert len(parsed) == 1001
    assert parsed[0][1] == 9.0


def test_asof_join_uses_calendar_days_not_grid_row_offsets():
    grid = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]  # Fri, Mon, Tue
    joined = tier_a.asof_join(grid, [(date(2026, 1, 2), 42.0)], "SPX")
    assert joined[0] == (None, None)
    assert joined[1] == (42.0, 2)  # Friday close modelled available Saturday
    assert joined[2] == (42.0, 3)


def test_release_rules_do_not_use_ingestion_time():
    nfci, quality = tier_a.release_instant(date(2026, 9, 18), "NFCI")
    assert nfci.astimezone(tier_a.NY).weekday() == 2
    assert nfci.astimezone(tier_a.NY).time().hour == 8
    assert quality == "known_release_rule"
