import json
import math
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from fxlab.denn import (
    age_decay, build_denn_baseline, build_spectral_baseline, build_spectral_stability, exponential_memory,
)
from fxlab.denn.pipeline import _asinh_change, _fit_ridge, _predict
from fxlab.denn.spectral import _load_config as load_spectral_config
from fxlab.denn.spectral import haar_energy, lag_correlations, welch_spectra
from fxlab.denn.stability import chronological_windows


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


def test_spectral_primitives_detect_known_frequency_and_lead():
    factor = [math.sin(2 * math.pi * index / 16) for index in range(1024)]
    target = [math.sin(2 * math.pi * (index - 4) / 16) for index in range(1024)]
    spectra = welch_spectra(factor, target, 256, 128)
    peak = max(spectra, key=lambda row: row["factor_power"])
    assert peak["period_sessions"] == pytest.approx(16)
    assert peak["coherence"] == pytest.approx(1)
    assert peak["factor_lead_sessions"] == pytest.approx(4, abs=0.05)

    pseudo_random = [float((index * 73 + index * index * 19) % 997) for index in range(400)]
    delayed = [0.0] * 4 + pseudo_random[:-4]
    strongest = max(lag_correlations(pseudo_random, delayed, 8), key=lambda row: abs(row["correlation"]))
    assert strongest["lag_sessions"] == 4
    assert strongest["correlation"] == pytest.approx(1)
    wavelets = haar_energy(factor, 6)
    assert len(wavelets) == 6
    assert all(0 <= row["energy_share"] <= 1 for row in wavelets)


def test_spectral_config_identity_ignores_platform_line_endings(tmp_path):
    source = Path("config/spectral.yaml").read_text(encoding="utf-8")
    lf_path, crlf_path = tmp_path / "lf.yaml", tmp_path / "crlf.yaml"
    lf_path.write_bytes(source.replace("\r\n", "\n").encode())
    crlf_path.write_bytes(source.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert load_spectral_config(lf_path)[1] == load_spectral_config(crlf_path)[1]


def test_stability_windows_include_latest_endpoint_without_duplicates():
    windows = chronological_windows(2500, 1024, 256, 1024, 256)
    rolling = [row for row in windows if row["mode"] == "rolling"]
    expanding = [row for row in windows if row["mode"] == "expanding"]
    assert rolling[-1]["end"] == 2500
    assert expanding[-1]["end"] == 2500
    assert len({(row["start"], row["end"]) for row in rolling}) == len(rolling)
    assert len({row["end"] for row in expanding}) == len(expanding)


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

    first_spectral = build_spectral_baseline()
    second_spectral = build_spectral_baseline()
    assert first_spectral["dataset_id"] == second_spectral["dataset_id"]
    assert first_spectral["normalized_sha256"] == second_spectral["normalized_sha256"]
    assert len(first_spectral["band_metrics"]) == 25
    assert len(first_spectral["strongest_lags"]) == 5
    with duckdb.connect() as connection:
        bands = str(tmp_path / first_spectral["files"]["bands"])
        wavelets = str(tmp_path / first_spectral["files"]["wavelets"])
        lags = str(tmp_path / first_spectral["files"]["lags"])
        assert connection.execute("SELECT count(*) FROM read_parquet(?)", [bands]).fetchone()[0] == 25
        assert connection.execute("SELECT count(*) FROM read_parquet(?)", [wavelets]).fetchone()[0] == 36
        assert connection.execute("SELECT count(*) FROM read_parquet(?)", [lags]).fetchone()[0] == 605

    first_stability = build_spectral_stability()
    second_stability = build_spectral_stability()
    assert first_stability["dataset_id"] == second_stability["dataset_id"]
    assert first_stability["normalized_sha256"] == second_stability["normalized_sha256"]
    assert len(first_stability["leaders"]) == 10
    assert len(first_stability["lag_one"]) == 10
    assert first_stability["rolling_windows"] > 0 and first_stability["expanding_windows"] > 0
    with duckdb.connect() as connection:
        band_summary = str(tmp_path / first_stability["files"]["band_summary"])
        lag_summary = str(tmp_path / first_stability["files"]["lag_summary"])
        assert connection.execute("SELECT count(*) FROM read_parquet(?)", [band_summary]).fetchone()[0] == 50
        assert connection.execute("SELECT count(*) FROM read_parquet(?)", [lag_summary]).fetchone()[0] == 40
