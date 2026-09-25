import json
import math
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from fxlab.denn import age_decay, build_denn_baseline, exponential_memory
from fxlab.denn.pipeline import _asinh_change, _fit_ridge, _predict


def test_decay_and_memory_half_life():
    assert age_decay(0, 10) == 1
    assert age_decay(10, 10) == pytest.approx(0.5)
    memory = exponential_memory([0, 1, 1], 1)
    assert memory == pytest.approx([0, 0.5, 0.75])
    with pytest.raises(ValueError):
        age_decay(-1, 1)


def test_ridge_solver_predicts_linear_relation():
    rows = [{"x": float(index), "y": 2.0 + 3.0 * index} for index in range(1, 30)]
    model = _fit_ridge(rows, ["x"], "y", 0.000001)
    assert _predict(model, {"x": 31.0}, ["x"]) == pytest.approx(95.0, rel=1e-6)


def test_oil_transform_accepts_negative_prices():
    values = [20.0] * 20 + [-37.63]
    assert math.isfinite(_asinh_change(values, 20, 20))


def test_full_denn_pipeline_is_deterministic_and_temporally_aligned(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    common = tmp_path / "gold/open_daily/source/common_d1.parquet"
    common.parent.mkdir(parents=True)
    json_path = tmp_path / "common.json"
    start = date(2004, 1, 1)
    rows = []
    for index in range(3300):
        day = start + timedelta(days=index)
        trend = index / 3300
        rows.append({
            "observation_date": str(day), "EURUSD_REF": 1.1 + 0.04 * math.sin(index / 37) + trend * 0.02,
            "US_2Y": 2.0 + math.sin(index / 80), "EA_2Y": 1.0 + math.sin(index / 90),
            "US_10Y": 3.0 + math.sin(index / 140), "EA_10Y": 2.0 + math.sin(index / 150),
            "BRENT": 60 + 8 * math.sin(index / 45), "WTI": 57 + 7 * math.sin(index / 43),
            "VIX": 20 + 3 * math.sin(index / 17),
        })
    json_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE common AS SELECT * FROM read_json_auto(?)", [str(json_path)])
        connection.execute("COPY common TO ? (FORMAT PARQUET)", [str(common)])
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "data_coverage.json").write_text(json.dumps({
        "dataset_id": "source", "normalized_sha256": "a" * 64,
        "generated_at": "2026-09-25T00:00:00+00:00",
        "files": {"common_d1": "gold/open_daily/source/common_d1.parquet"},
    }), encoding="utf-8")

    first = build_denn_baseline()
    second = build_denn_baseline()
    assert first["dataset_id"] == second["dataset_id"]
    assert first["normalized_sha256"] == second["normalized_sha256"]
    assert first["snapshot_rows"] == len(rows) * 8
    assert first["fold_rows"] > 0 and first["prediction_rows"] > 0
    with duckdb.connect() as connection:
        features = str(tmp_path / first["files"]["features"])
        snapshots = str(tmp_path / first["files"]["snapshots"])
        assert connection.execute(
            'SELECT count(*) FROM read_parquet(?) WHERE target_start_1d <= feature_date OR target_end_60d <= feature_date',
            [features],
        ).fetchone()[0] == 0
        types = {row[0]: row[1] for row in connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [snapshots]).fetchall()}
        assert types["available_at"] == "TIMESTAMP WITH TIME ZONE"
        assert types["ingested_at"] == "TIMESTAMP WITH TIME ZONE"
        assert {"source", "unit", "revision_id", "source_snapshot_id", "time_quality"} <= set(types)
        provenance = connection.execute(
            "SELECT count(DISTINCT source_snapshot_id), "
            "count(*) FILTER (WHERE source IS NULL OR unit IS NULL) FROM read_parquet(?)", [snapshots]
        ).fetchone()
        assert provenance == (1, 0)
