import json
import math
from datetime import date, timedelta

import duckdb

from fxlab.denn.market_confirmation import build_market_confirmation


def _common_file(path, target_name, prices, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.execute(
            f'CREATE TABLE data(observation_date DATE,"{target_name}" DOUBLE,"US_2Y" DOUBLE,"EA_2Y" DOUBLE,'
            '"US_10Y" DOUBLE,"EA_10Y" DOUBLE,"BRENT" DOUBLE,"WTI" DOUBLE,"VIX" DOUBLE)'
        )
        connection.executemany(
            "INSERT INTO data VALUES (?,?,?,?,?,?,?,?,?)",
            [(row[0], prices[index], *row[1:]) for index, row in enumerate(rows)],
        )
        connection.execute("COPY data TO ? (FORMAT PARQUET)", [str(path)])


def test_market_confirmation_is_reproducible(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    start = date(2010, 1, 1)
    rows = []
    market, reference = [], []
    market_level = reference_level = 1.2
    for index in range(1100):
        signal = math.sin(index / 17) * 0.01 + math.cos(index / 31) * 0.005
        market_level *= math.exp(-0.003 * signal + 0.0002 * math.sin(index / 5))
        reference_level *= math.exp(-0.002 * signal + 0.0001 * math.cos(index / 7))
        market.append(market_level)
        reference.append(reference_level)
        rows.append((start + timedelta(days=index), 2.0 + signal, 1.0, 3.0 + signal / 2, 2.0,
                     60 + math.sin(index / 13), 55 + math.cos(index / 11), 20 + math.sin(index / 9)))

    market_path = tmp_path / "gold/open/market.parquet"
    reference_path = tmp_path / "gold/open/reference.parquet"
    _common_file(market_path, "EURUSD", market, rows)
    _common_file(reference_path, "EURUSD_REF", reference, rows)
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "data_coverage.json").write_text(json.dumps({
        "dataset_id": "coverage", "normalized_sha256": "a" * 64,
        "files": {"common_market_d1": "gold/open/market.parquet", "common_d1": "gold/open/reference.parquet"},
    }))

    first = build_market_confirmation()
    second = build_market_confirmation()
    assert first["dataset_id"] == second["dataset_id"]
    assert first["observations"] == 1099
    assert len(first["full_correlations"]) == 40
    assert first["rolling_windows"] == 2
    assert {row["target"] for row in first["rolling_summaries"]} == {"market", "reference"}
    for relative in first["files"].values():
        assert (tmp_path / relative).exists()
