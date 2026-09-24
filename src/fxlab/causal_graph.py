"""Validated M6 causal-hypothesis graph and portable exports."""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import networkx as nx
import yaml

from .store import atomic_json, root

UTC = timezone.utc
PARSER = "causal-hypothesis-graph-1"


class GraphDefinitionError(ValueError):
    pass


def config_path(name: str) -> Path:
    project = Path(os.environ.get("FXLAB_PROJECT", "."))
    return project / "config" / name


def _load_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise GraphDefinitionError(f"Cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise GraphDefinitionError(f"Top level of {path} must be a mapping")
    return value


def _unique(items: list[dict], kind: str) -> dict[str, dict]:
    result = {}
    for item in items:
        identifier = item.get("id") if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier:
            raise GraphDefinitionError(f"Every {kind} requires a non-empty id")
        if identifier in result:
            raise GraphDefinitionError(f"Duplicate {kind} id: {identifier}")
        result[identifier] = item
    return result


def validate_graph(schema: dict, definition: dict) -> nx.DiGraph:
    for key in ("node_types", "edge_types", "signs", "horizon_units", "evidence_statuses", "data_statuses"):
        if not isinstance(schema.get(key), list) or not schema[key]:
            raise GraphDefinitionError(f"Schema field {key} must be a non-empty list")
    nodes = _unique(definition.get("nodes", []), "node")
    edges = _unique(definition.get("edges", []), "edge")
    chains = _unique(definition.get("chains", []), "chain")
    if not nodes or not edges or not chains:
        raise GraphDefinitionError("Graph requires nodes, edges and chains")

    required_nodes = set(schema.get("required_node_fields", []))
    required_edges = set(schema.get("required_edge_fields", []))
    node_types, data_statuses = set(schema["node_types"]), set(schema["data_statuses"])
    edge_types, signs = set(schema["edge_types"]), set(schema["signs"])
    horizon_units, evidence = set(schema["horizon_units"]), set(schema["evidence_statuses"])

    graph = nx.DiGraph()
    for identifier, node in nodes.items():
        missing = required_nodes - node.keys()
        if missing:
            raise GraphDefinitionError(f"Node {identifier} misses: {', '.join(sorted(missing))}")
        if node["type"] not in node_types:
            raise GraphDefinitionError(f"Node {identifier} has unknown type {node['type']}")
        if node["data_status"] not in data_statuses:
            raise GraphDefinitionError(f"Node {identifier} has unknown data status {node['data_status']}")
        if not isinstance(node["order"], int) or node["order"] < 0:
            raise GraphDefinitionError(f"Node {identifier} order must be a non-negative integer")
        graph.add_node(identifier, **{key: value for key, value in node.items() if key != "id"})

    for identifier, edge in edges.items():
        missing = required_edges - edge.keys()
        if missing:
            raise GraphDefinitionError(f"Edge {identifier} misses: {', '.join(sorted(missing))}")
        source, target = edge["source"], edge["target"]
        if source not in nodes or target not in nodes:
            raise GraphDefinitionError(f"Edge {identifier} references an unknown node")
        if source == target:
            raise GraphDefinitionError(f"Self edge is not allowed: {identifier}")
        if edge["type"] not in edge_types or edge["sign"] not in signs:
            raise GraphDefinitionError(f"Edge {identifier} has an unknown type or sign")
        if edge["horizon_unit"] not in horizon_units or edge["evidence_status"] not in evidence:
            raise GraphDefinitionError(f"Edge {identifier} has an unknown horizon unit or evidence status")
        low, high = edge["lag_min"], edge["lag_max"]
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)) or low < 0 or high < low:
            raise GraphDefinitionError(f"Edge {identifier} has an invalid lag interval")
        conditioning = edge.get("conditioning_on", [])
        if not isinstance(conditioning, list) or any(node_id not in nodes for node_id in conditioning):
            raise GraphDefinitionError(f"Edge {identifier} has invalid conditioning nodes")
        graph.add_edge(source, target, edge_id=identifier,
                       **{key: value for key, value in edge.items() if key not in {"id", "source", "target"}})

    if not nx.is_directed_acyclic_graph(graph):
        cycle = " -> ".join(source for source, _ in nx.find_cycle(graph))
        raise GraphDefinitionError(f"Hypothesis graph must be acyclic; cycle: {cycle}")

    for identifier, chain in chains.items():
        node_ids, edge_ids = chain.get("node_ids"), chain.get("edge_ids")
        if not isinstance(node_ids, list) or not node_ids or any(node_id not in nodes for node_id in node_ids):
            raise GraphDefinitionError(f"Chain {identifier} has invalid nodes")
        if not isinstance(edge_ids, list) or not edge_ids or any(edge_id not in edges for edge_id in edge_ids):
            raise GraphDefinitionError(f"Chain {identifier} has invalid edges")
        if any(edges[edge_id]["source"] not in node_ids or edges[edge_id]["target"] not in node_ids
               for edge_id in edge_ids):
            raise GraphDefinitionError(f"Chain {identifier} contains an edge outside its node set")
    return graph


def load_graph(schema_path: Path | None = None, graph_path: Path | None = None) -> tuple[dict, dict, nx.DiGraph]:
    schema = _load_yaml(schema_path or config_path("graph_schema.yaml"))
    definition = _load_yaml(graph_path or config_path("causal_graph.yaml"))
    return schema, definition, validate_graph(schema, definition)


def _graphml_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_graphml(graph: nx.DiGraph, path: Path) -> None:
    portable = nx.DiGraph()
    for identifier, attributes in graph.nodes(data=True):
        portable.add_node(identifier, **{key: _graphml_value(value) for key, value in attributes.items()})
    for source, target, attributes in graph.edges(data=True):
        portable.add_edge(source, target, **{key: _graphml_value(value) for key, value in attributes.items()})
    temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
    nx.write_graphml(portable, temporary, encoding="utf-8")
    temporary.replace(path)


def build_causal_graph(schema_path: Path | None = None, graph_path: Path | None = None) -> dict:
    schema_file = schema_path or config_path("graph_schema.yaml")
    definition_file = graph_path or config_path("causal_graph.yaml")
    schema, definition, graph = load_graph(schema_file, definition_file)
    canonical = json.dumps({"schema": schema, "graph": definition}, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":"))
    normalized_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    schema_sha256 = hashlib.sha256(schema_file.read_bytes()).hexdigest()
    definition_sha256 = hashlib.sha256(definition_file.read_bytes()).hexdigest()
    signature = {"parser": PARSER, "schema_sha256": schema_sha256,
                 "definition_sha256": definition_sha256, "normalized_sha256": normalized_sha256}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    folder = root() / "graph" / dataset_id
    folder.mkdir(parents=True, exist_ok=True)

    nodes = [{"id": identifier, **attributes} for identifier, attributes in graph.nodes(data=True)]
    links = [{"source": source, "target": target, **attributes}
             for source, target, attributes in graph.edges(data=True)]
    chains = []
    node_lookup = {item["id"]: item for item in nodes}
    for chain in definition["chains"]:
        blocked = [identifier for identifier in chain["node_ids"]
                   if node_lookup[identifier]["data_status"] != "available_non_strict"]
        chains.append({**chain, "status": "blocked" if blocked else "non_strict_only",
                       "strict_ready": False, "blocked_node_ids": blocked})

    graph_json = {
        "dataset_id": dataset_id,
        "graph_version": definition["graph_version"],
        "title": definition["title"],
        "description": definition["description"],
        "nodes": nodes,
        "links": links,
        "chains": chains,
    }
    graph_json_path = folder / "causal_graph.json"
    graphml_path = folder / "causal_graph.graphml"
    atomic_json(graph_json_path, graph_json)
    _write_graphml(graph, graphml_path)

    data_counts = Counter(node["data_status"] for node in nodes)
    evidence_counts = Counter(edge["evidence_status"] for edge in links)
    report = {
        "dataset_id": dataset_id,
        "parser": PARSER,
        "graph_version": definition["graph_version"],
        "normalized_sha256": normalized_sha256,
        "schema_sha256": schema_sha256,
        "definition_sha256": definition_sha256,
        "node_count": len(nodes),
        "edge_count": len(links),
        "chain_count": len(chains),
        "data_status": dict(sorted(data_counts.items())),
        "evidence_status": dict(sorted(evidence_counts.items())),
        "strict_ready_chains": sum(chain["strict_ready"] for chain in chains),
        "is_dag": True,
        "files": {
            "json": graph_json_path.relative_to(root()).as_posix(),
            "graphml": graphml_path.relative_to(root()).as_posix(),
        },
        "limitations": [
            "Edges encode research hypotheses and temporal constraints, not identified causal effects.",
            "Available nodes remain non-strict because historical receipt or market vintage is unverified.",
            "No observation-level market or macro time series is duplicated into the graph.",
            "Blocked chains remain visible; missing consensus, futures/OIS and yield data are not synthesized.",
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder / "manifest.json", report)
    atomic_json(root() / "reports" / "causal_graph.json", report)
    return report
