import csv
import io
from datetime import date

import duckdb
import pytest

import fxlab.open_data as open_data


TREASURY_XML = b'''<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
  <entry><content type="application/xml"><m:properties>
    <d:NEW_DATE>2024-01-02T00:00:00</d:NEW_DATE><d:BC_2YEAR>4.33</d:BC_2YEAR><d:BC_10YEAR>3.95</d:BC_10YEAR>
  </m:properties></content></entry>
</feed>'''


def test_treasury_parser_requires_both_maturities():
    parsed = open_data.parse_treasury_xml(TREASURY_XML)
    assert parsed["US_2Y"] == [(date(2024, 1, 2), 4.33)]
    assert parsed["US_10Y"] == [(date(2024, 1, 2), 3.95)]
    with pytest.raises(ValueError, match="lacks"):
        open_data.parse_treasury_xml(TREASURY_XML.replace(b"BC_10YEAR", b"OTHER"))


def test_ecb_and_vix_parsers_reject_short_or_wrong_series():
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["KEY", "TIME_PERIOD", "OBS_VALUE"])
    writer.writeheader()
    for ordinal in range(1001):
        writer.writerow({"KEY": "expected", "TIME_PERIOD": date.fromordinal(date(2020, 1, 1).toordinal() + ordinal),
                         "OBS_VALUE": "1.25"})
    assert len(open_data.parse_ecb_csv(output.getvalue().encode(), "expected")) == 1001
    with pytest.raises(ValueError, match="short"):
        open_data.parse_ecb_csv(output.getvalue().encode(), "wrong")

    vix = io.StringIO()
    writer = csv.DictWriter(vix, fieldnames=["DATE", "CLOSE"])
    writer.writeheader()
    for ordinal in range(5000):
        day = date.fromordinal(date(2000, 1, 1).toordinal() + ordinal)
        writer.writerow({"DATE": day.strftime("%m/%d/%Y"), "CLOSE": "20.5"})
    assert len(open_data.parse_vix_csv(vix.getvalue().encode())) == 5000


def test_coverage_builds_actual_parquet_and_derived_spreads(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    days = [(date(2005, 1, 3), 1.0), (date(2005, 1, 4), 2.0)]
    metadata = {"source_url": "https://example.test/source", "sha256": "a" * 64,
                "ingested_at": "2026-09-25T00:00:00+00:00"}

    monkeypatch.setattr(open_data, "_fetch", lambda *args, **kwargs: (b"fixture", metadata))
    monkeypatch.setattr(open_data, "parse_treasury_xml", lambda body: {"US_2Y": days, "US_10Y": days})
    monkeypatch.setattr(open_data, "parse_ecb_csv", lambda body, key: [(day, value / 2) for day, value in days])
    monkeypatch.setattr(open_data, "parse_eia_xls", lambda body: days)
    monkeypatch.setattr(open_data, "parse_vix_csv", lambda body: days)
    monkeypatch.setattr(open_data, "_load_eurusd", lambda start, end: (days, {"snapshots": [metadata]}))
    monkeypatch.setattr(open_data, "_load_eurusd_reference", lambda start, end: (days, {
        "source_url": metadata["source_url"], "raw_sha256": metadata["sha256"], "ingested_at": metadata["ingested_at"]}))

    report = open_data.build_open_data_coverage(date(2005, 1, 3), date(2005, 1, 4))
    assert report["counts"]["continuous_d1"] == 11
    assert report["common_overlap"]["rows"] == 2
    assert report["optional_premium"][0]["status"] == "OPTIONAL"

    spread = tmp_path / report["files"]["US_EA_2Y"]
    common = tmp_path / report["files"]["common_d1"]
    with duckdb.connect() as connection:
        assert connection.execute("SELECT value FROM read_parquet(?) ORDER BY observation_date", [str(spread)]).fetchall() == [(0.5,), (1.0,)]
        assert connection.execute("SELECT count(*) FROM read_parquet(?)", [str(common)]).fetchone()[0] == 2

    def missing_eurusd(*_args):
        raise FileNotFoundError("Dukascopy D1 is absent")

    monkeypatch.setattr(open_data, "_load_eurusd", missing_eurusd)
    without_auxiliary_price = open_data.build_open_data_coverage(date(2005, 1, 3), date(2005, 1, 4))
    assert without_auxiliary_price["counts"]["continuous_d1"] == 10
    assert without_auxiliary_price["counts"]["missing_optional"] == 1
    assert without_auxiliary_price["unavailable_series"][0]["series_id"] == "EURUSD"
    assert "EURUSD" not in without_auxiliary_price["files"]
    assert without_auxiliary_price["common_overlap"]["rows"] == 2
