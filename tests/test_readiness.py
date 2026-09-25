import json

from fxlab import readiness


def sample_graph():
    nodes = [{"id": node} for item in readiness.INVESTMENTS for node in item["nodes"]]
    chains = [
        {"id": "cpi_us", "node_ids": ["us_cpi_surprise"]},
        {"id": "nfp_us", "node_ids": ["us_nfp_surprise"]},
        {"id": "fomc", "node_ids": ["fomc_path_surprise", "fed_rate_expectation"]},
        {"id": "ecb", "node_ids": ["ecb_policy_surprise", "ecb_rate_expectation"]},
        {"id": "spread", "node_ids": ["us_2y_yield", "eu_2y_yield", "us_eu_2y_spread"]},
        {"id": "oil", "node_ids": ["oil_price", "us_inflation_expectation", "eu_inflation_expectation"]},
        {"id": "positioning", "node_ids": ["cftc_eur_positioning"]},
    ]
    return {"nodes": nodes, "chains": chains}


def test_rank_investments_is_deterministic_and_transparent():
    baseline = {"replications": [{"id": item} for item in [
        "macro-surprise-fx-jump", "us-macro-interdealer-fx", "realtime-fundamentals-eurusd",
        "fomc-path-surprise-fx", "ecb-policy-surprises-eurusd"]]}
    interactions = {"planned_interactions": [{"id": item} for item in [
        "surprise-x-positioning", "surprise-x-market-regime", "rate-differential-x-risk-sentiment",
        "oil-x-inflation-expectations"]]}
    first = readiness.rank_investments(sample_graph(), baseline, interactions)
    second = readiness.rank_investments(sample_graph(), baseline, interactions)
    assert first == second
    assert first[0]["id"] == "historical_consensus_intraday_fx"
    assert first[0]["research_unlock_score"] == 2 + 2 + 3 * 3 + 2 * 2
    assert [item["priority_rank"] for item in first] == list(range(1, 6))
    assert all(item["cost_status"] == "unknown" for item in first)


def test_build_readiness_review_has_stable_identity(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    graph_dir = tmp_path / "graph"
    reports.mkdir()
    graph_dir.mkdir()
    graph = sample_graph()
    (graph_dir / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    fixtures = {
        "bars.json": {"dataset_id": "b", "normalized_sha256": "b1", "h1_rows": 10, "d1_rows": 2},
        "macro_releases.json": {"dataset_id": "m", "normalized_sha256": "m1", "observation_rows": 3},
        "cftc.json": {"dataset_id": "c", "normalized_sha256": "c1", "rows": 4},
        "fomc.json": {"dataset_id": "f", "normalized_sha256": "f1"},
        "ecb_policy.json": {"dataset_id": "e", "normalized_sha256": "e1"},
        "event_alignment.json": {"dataset_id": "a", "normalized_sha256": "a1", "rows": 5, "strict_pit_rows": 0},
        "baseline_experiments.json": {"dataset_id": "x", "normalized_sha256": "x1", "result_rows": 6,
                                       "replication_status": {"available": 0}, "replications": []},
        "causal_graph.json": {"dataset_id": "g", "normalized_sha256": "g1", "node_count": len(graph["nodes"]),
                              "edge_count": 12, "chain_count": 7, "strict_ready_chains": 0,
                              "files": {"json": "graph/graph.json"}},
        "interaction_experiments.json": {"dataset_id": "i", "normalized_sha256": "i1", "feature_rows": 7,
                                         "result_rows": 8, "strict_pit_rows": 0, "planned_interactions": []},
    }
    for name, value in fixtures.items():
        (reports / name).write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(readiness, "root", lambda: tmp_path)
    one = readiness.build_readiness_review()
    two = readiness.build_readiness_review()
    assert one["dataset_id"] == two["dataset_id"]
    assert one["normalized_sha256"] == two["normalized_sha256"]
    assert one["generated_at"] != ""
    assert one["decision"]["open_spg"] == "defer"
    assert one["decision"]["gnn_ml"] == "defer"
    assert one["gates"] == {"strict_pit_rows": 0, "published_replications_available": 0,
                            "strict_graph_chains": 0, "strict_interaction_rows": 0}
