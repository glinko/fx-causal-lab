from datetime import date

import pytest

from fxlab import eia


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.nrows = len(rows)

    def row_values(self, index):
        return self.rows[index]

    def cell_value(self, row, column):
        return self.rows[row][column]


def test_weekly_parser_selects_source_key_and_rejects_duplicates():
    rows = [
        ["title", ""], ["Sourcekey", "WCRFPUS2"], ["Date", "Production"],
        [1, 100.0], [2, 101.0],
    ]
    parsed = eia._parse_sheet(FakeSheet(rows), "WCRFPUS2", lambda value: date(2020, 1, int(value)), 2)
    assert parsed == [(date(2020, 1, 1), 100.0), (date(2020, 1, 2), 101.0)]
    rows.append([2, 102.0])
    with pytest.raises(ValueError, match="Duplicate"):
        eia._parse_sheet(FakeSheet(rows), "WCRFPUS2", lambda value: date(2020, 1, int(value)), 2)


def test_normalization_waits_until_following_friday():
    meta = {"ingested_at": "2026-09-27T00:00:00+00:00", "source_url": "https://example.test/eia.xls",
            "sha256": "a" * 64}
    rows = eia.normalize_series("EIA_US_CRUDE_PRODUCTION", [(date(2026, 9, 18), 13939.0)], meta,
                                date(2026, 1, 1), date(2026, 12, 31))
    assert rows[0]["available_at"].isoformat() == "2026-09-25T00:00:00-04:00"
    assert rows[0]["time_quality"] == "inferred_conservative"
    assert rows[0]["strict_pit_eligible"] is False
