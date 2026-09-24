import copy
import json
from pathlib import Path

import networkx as nx
import pytest
import yaml
from fastapi.testclient import TestClient

from fxlab.causal_graph import GraphDefinitionError, build_causal_graph, load_graph, validate_graph
from fxlab.web import app

PROJECT = Path(__file__).parents[1]
SCHEMA = PROJECT / "config" / "graph_schema.yaml"
DEFINITION = PROJECT / "config" / "causal_graph.yaml"


def test_repository_graph_is_valid_dag():
    schema, definition, graph = load_graph(SCHEMA, DEFINITION)
    assert nx.is_directed_acyclic_graph(graph)
    assert len(graph.nodes) == 18
    assert len(graph.edges) == 18
    assert len(definition["chains"]) == 7
    assert "MODERATES" in schema["edge_types"]


def test_invalid_cycle_and_unknown_reference_are_rejected():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    definition = yaml.safe_load(DEFINITION.read_text(encoding="utf-8"))
    cyclic = copy.deepcopy(definition)
    cyclic["edges"].append({"id": "fx_back_to_cpi", "source": "eurusd", "target": "us_cpi_release",
                            "type": "CAUSES_HYPOTHESIS", "sign": "unknown", "lag_min": 0, "lag_max": 1,
                            "horizon_unit": "trading_days", "evidence_status": "untested"})
    with pytest.raises(GraphDefinitionError, match="acyclic"):
        validate_graph(schema, cyclic)
    broken = copy.deepcopy(definition)
    broken["edges"][0]["target"] = "missing_node"
    with pytest.raises(GraphDefinitionError, match="unknown node"):
        validate_graph(schema, broken)


def test_deterministic_json_and_graphml_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    first = build_causal_graph(SCHEMA, DEFINITION)
    second = build_causal_graph(SCHEMA, DEFINITION)
    assert first["dataset_id"] == second["dataset_id"]
    assert first["normalized_sha256"] == second["normalized_sha256"]
    assert first["strict_ready_chains"] == 0
    assert first["data_status"] == {"available_non_strict": 6, "unavailable": 12}
    graph_json = json.loads((tmp_path / first["files"]["json"]).read_text(encoding="utf-8"))
    assert len(graph_json["chains"]) == 7
    assert all(chain["status"] == "blocked" for chain in graph_json["chains"])
    exported = nx.read_graphml(tmp_path / first["files"]["graphml"])
    assert len(exported.nodes) == 18 and len(exported.edges) == 18


def test_graph_web_api_and_report(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    client = TestClient(app)
    assert client.get("/reports/causal-graph").status_code == 200
    assert client.get("/api/causal-graph").json()["nodes"] == []
    report = build_causal_graph(SCHEMA, DEFINITION)
    response = client.get("/api/causal-graph")
    assert response.status_code == 200 and len(response.json()["nodes"]) == 18
    assert client.get("/reports/causal-graph").status_code == 200
    assert client.get("/download/causal_graph.json").status_code == 200
    assert client.get("/download/causal_graph.graphml").status_code == 200
    assert report["is_dag"] is True
