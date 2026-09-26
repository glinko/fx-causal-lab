"""Contract tests for the pre-registered expanded Tier A grouped suite."""
from datetime import date, timedelta
from pathlib import Path
import json

import duckdb
import pytest
import yaml

from fxlab.denn import grouped_tier_a as gt

CONFIG = Path(__file__).resolve().parents[1] / "config" / "grouped_tier_a.yaml"


def test_config_freezes_partition_family_and_exclusions():
    config, digest = gt._load_config(CONFIG)
    members = [member for block in config["blocks"] for member in block["members"]]
    assert config["version"] == "denn-grouped-tier-a-1"
    assert sorted(members) == sorted(config["features"])
    assert config["multiplicity"]["family_size"] == len(config["blocks"]) == 7
    assert config["sample"]["no_imputation"] is True
    assert "tic_total_change_12m" in config["sample"]["excluded_insufficient_history"]
    assert len(digest) == 64


def test_config_rejects_overlap_and_wrong_family(tmp_path):
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["blocks"][1]["members"].append(config["blocks"][0]["members"][0])
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="partition"):
        gt._load_config(bad)
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["multiplicity"]["family_size"] = 6
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="family_size"):
        gt._load_config(bad)


def test_loader_uses_complete_cases_without_imputation(tmp_path, monkeypatch):
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    data = tmp_path
    (data / "reports").mkdir()
    (data / "gold").mkdir()
    rows = []
    start = date(2007, 5, 4)
    for index in range(2601):
        day = start + timedelta(days=index)
        values = [day] + [float(index + offset + 1) for offset in range(len(config["features"]))]
        for horizon in config["horizons"]:
            values += [0.001 * ((index % 7) - 3), day + timedelta(days=horizon)]
        rows.append(values)
    rows[100][1] = None
    parquet = data / "gold" / "features.parquet"
    with duckdb.connect() as connection:
        definitions = ["feature_date DATE"] + [f'"{name}" DOUBLE' for name in config["features"]]
        for horizon in config["horizons"]:
            definitions += [f"target_return_{horizon}d DOUBLE", f"target_end_{horizon}d DATE"]
        connection.execute(f"CREATE TABLE matrix ({','.join(definitions)})")
        connection.executemany(f"INSERT INTO matrix VALUES ({','.join('?' for _ in definitions)})", rows)
        connection.execute("COPY matrix TO ? (FORMAT PARQUET)", [str(parquet)])
    (data / "reports" / "tier_a_features.json").write_text(json.dumps({
        "dataset_id": "fixture", "files": {"features": "gold/features.parquet"}
    }), encoding="utf-8")
    monkeypatch.setattr(gt, "root", lambda: data)
    loaded, parent = gt._load_rows(config)
    assert len(loaded) == 2600
    assert parent["dataset_id"] == "fixture"
    assert loaded[0]["feature_date"] == start
