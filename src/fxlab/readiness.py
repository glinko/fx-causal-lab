"""M0–M7 review and a transparent post-MVP decision gate."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .store import atomic_json, root

UTC = timezone.utc
PARSER = "m0-m7-readiness-review-1"

REPORTS = {
    "bars": "bars.json",
    "macro": "macro_releases.json",
    "cftc": "cftc.json",
    "fomc": "fomc.json",
    "ecb": "ecb_policy.json",
    "alignment": "event_alignment.json",
    "baseline": "baseline_experiments.json",
    "graph": "causal_graph.json",
    "interactions": "interaction_experiments.json",
}

INVESTMENTS = [
    {
        "id": "historical_consensus_intraday_fx",
        "title": "Historical consensus + narrow-window FX",
        "nodes": ["us_cpi_surprise", "us_nfp_surprise"],
        "replications": ["macro-surprise-fx-jump", "us-macro-interdealer-fx", "realtime-fundamentals-eurusd"],
        "interactions": ["surprise-x-positioning", "surprise-x-market-regime"],
        "next_check": "Verify PIT depth, forecast vintages, licensing and minute-FX coverage before procurement.",
    },
    {
        "id": "policy_surprise_factors",
        "title": "Futures/OIS policy surprise factors",
        "nodes": ["fomc_path_surprise", "ecb_policy_surprise", "fed_rate_expectation", "ecb_rate_expectation"],
        "replications": ["fomc-path-surprise-fx", "ecb-policy-surprises-eurusd"],
        "interactions": [],
        "next_check": "Audit futures/OIS history, timestamp granularity and ECB press-conference windows.",
    },
    {
        "id": "comparable_2y_yields",
        "title": "Comparable point-in-time US/EA 2Y yields",
        "nodes": ["us_2y_yield", "eu_2y_yield", "us_eu_2y_spread"],
        "replications": [],
        "interactions": ["rate-differential-x-risk-sentiment"],
        "next_check": "Choose the euro-area proxy, vintage policy and availability timestamp contract.",
    },
    {
        "id": "oil_inflation_expectations",
        "title": "Oil and inflation-expectation vintages",
        "nodes": ["oil_price", "us_inflation_expectation", "eu_inflation_expectation"],
        "replications": [],
        "interactions": ["oil-x-inflation-expectations"],
        "next_check": "Verify versioned oil and breakeven/survey series before adding adapters.",
    },
    {
        "id": "strict_cftc_history",
        "title": "Historical CFTC release availability",
        "nodes": ["cftc_eur_positioning"],
        "replications": [],
        "interactions": ["surprise-x-positioning"],
        "next_check": "Recover actual release timestamps and exceptional delays, then extend positioning history.",
    },
]


def rank_investments(graph: dict, baseline: dict, interactions: dict) -> list[dict]:
    graph_nodes = {node["id"] for node in graph["nodes"]}
    replication_ids = {item["id"] for item in baseline["replications"]}
    interaction_ids = {item["id"] for item in interactions["planned_interactions"]}
    ranked = []
    for investment in INVESTMENTS:
        nodes = set(investment["nodes"]) & graph_nodes
        replications = set(investment["replications"]) & replication_ids
        planned = set(investment["interactions"]) & interaction_ids
        chains = [chain["id"] for chain in graph["chains"] if nodes & set(chain["node_ids"])]
        score = len(nodes) + 3*len(replications) + 2*len(planned) + len(chains)
        ranked.append({
            **investment,
            "node_count": len(nodes),
            "chains_touched": chains,
            "replications_touched": sorted(replications),
            "interactions_touched": sorted(planned),
            "research_unlock_score": score,
            "cost_status": "unknown",
        })
    ranked.sort(key=lambda item: (-item["research_unlock_score"], item["id"]))
    for index, item in enumerate(ranked, 1):
        item["priority_rank"] = index
    return ranked


def milestone_review(reports: dict) -> list[dict]:
    bars, macro, cftc = reports["bars"], reports["macro"], reports["cftc"]
    alignment, baseline = reports["alignment"], reports["baseline"]
    graph, interactions = reports["graph"], reports["interactions"]
    return [
        {"id": "M0", "title": "Source reconnaissance", "status": "partial",
         "evidence": "Public endpoints and samples are recorded; licensing, full depth and several PIT contracts remain open."},
        {"id": "M1", "title": "Core data model", "status": "mvp_complete",
         "evidence": "Bronze/Silver/Gold, four timestamps, provenance, nullable vintages and deterministic manifests are implemented."},
        {"id": "M2", "title": "EUR/USD H1/D1", "status": "mvp_complete_non_strict",
         "evidence": f'{bars["h1_rows"]} H1 and {bars["d1_rows"]} D1 rows; historical market vintage remains unverified.'},
        {"id": "M3", "title": "Official macro adapters", "status": "partial",
         "evidence": f'BLS {macro["observation_rows"]} observations; CFTC {cftc["rows"]} rows; FOMC/ECB decisions loaded; consensus/yields/oil absent.'},
        {"id": "M4", "title": "Event alignment", "status": "complete_non_strict",
         "evidence": f'{alignment["rows"]} aligned rows with 1/5/20/60-session targets and anti-leakage checks; strict rows {alignment["strict_pit_rows"]}.'},
        {"id": "M5", "title": "Published-effect baseline", "status": "descriptive_complete",
         "evidence": f'{baseline["result_rows"]} baseline rows; {baseline["replication_status"]["available"]} published replications available.'},
        {"id": "M6", "title": "Causal graph prototype", "status": "mvp_complete",
         "evidence": f'{graph["node_count"]} nodes, {graph["edge_count"]} edges, {graph["chain_count"]} chains; strict-ready {graph["strict_ready_chains"]}.'},
        {"id": "M7", "title": "Interaction experiments", "status": "descriptive_complete",
         "evidence": f'{interactions["feature_rows"]} as-of feature rows and {interactions["result_rows"]} summaries; strict rows {interactions["strict_pit_rows"]}.'},
    ]


def build_readiness_review() -> dict:
    reports = {}
    for key, name in REPORTS.items():
        path = root()/"reports"/name
        if not path.exists():
            raise ValueError(f"Required report missing: {name}")
        reports[key] = json.loads(path.read_text(encoding="utf-8"))
    graph_report = reports["graph"]
    graph = json.loads((root()/graph_report["files"]["json"]).read_text(encoding="utf-8"))
    priorities = rank_investments(graph, reports["baseline"], reports["interactions"])
    milestones = milestone_review(reports)
    inputs = {key: {"dataset_id": value.get("dataset_id"), "normalized_sha256": value.get("normalized_sha256")}
              for key, value in reports.items()}
    decision = {
        "technical_mvp": "proven_end_to_end",
        "strict_research": "blocked_by_point_in_time_data",
        "open_spg": "defer",
        "gnn_ml": "defer",
        "web_ui": "keep_current_scope",
        "next_action": priorities[0]["id"],
        "reason": "Data availability blocks the named hypotheses; additional platform complexity would not create the missing evidence.",
    }
    normalized = {"inputs": inputs, "milestones": milestones, "priorities": priorities, "decision": decision}
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    signature = {"parser": PARSER, "normalized_sha256": normalized_sha256}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    report = {
        "dataset_id": dataset_id,
        "parser": PARSER,
        "normalized_sha256": normalized_sha256,
        "inputs": inputs,
        "milestones": milestones,
        "milestone_status_counts": {status: sum(item["status"] == status for item in milestones)
                                    for status in sorted({item["status"] for item in milestones})},
        "priorities": priorities,
        "score_method": "graph nodes + touched graph chains + 3 × published replications + 2 × planned interactions",
        "score_limitations": "Research-unlock score measures documented coverage only; cost, licensing and source quality remain unknown.",
        "decision": decision,
        "gates": {
            "strict_pit_rows": reports["alignment"]["strict_pit_rows"],
            "published_replications_available": reports["baseline"]["replication_status"]["available"],
            "strict_graph_chains": graph_report["strict_ready_chains"],
            "strict_interaction_rows": reports["interactions"]["strict_pit_rows"],
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(root()/"reports"/"readiness_review.json", report)
    return report
