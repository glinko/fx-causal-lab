import json
import os
from pathlib import Path
from typing import Literal

import duckdb
import markdown
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .recon import CONFIG
from .store import root
from . import __version__

PACKAGE = Path(__file__).parent
PROJECT = Path(os.environ.get("FXLAB_PROJECT", "."))
app = FastAPI(title="FX Causal Lab", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE / "templates")
DOCS = {"decisions": "DECISIONS.md", "questions": "OPEN_QUESTIONS.md", "sources": "SOURCE_MATRIX.md",
        "deployment": "DEPLOYMENT.md", "denn": "docs/DENN_SPEC_RU.md", "roadmap": "ROADMAP_RU.md"}


def read_json(name, default):
    path = root() / "reports" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
    return response


@app.get("/healthz")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "market": read_json("market.json", None),
        "bars": read_json("bars.json", None),
        "sources": read_json("sources.json", json.loads(CONFIG.read_text(encoding="utf-8"))),
    })


@app.get("/api/market")
def market(limit: int = Query(800, ge=1, le=10000)):
    path = root() / "silver" / "ecb_eurusd.parquet"
    if not path.exists():
        return {"points": [], "report": None}
    with duckdb.connect() as connection:
        rows = connection.execute("SELECT date, value FROM read_parquet(?) ORDER BY date DESC LIMIT ?", [str(path), limit]).fetchall()
    return {"points": [{"date": str(d), "value": v} for d, v in reversed(rows)], "report": read_json("market.json", None)}


@app.get("/api/sources")
def sources():
    return read_json("sources.json", [])


@app.get("/api/bars")
def bars(frequency: Literal["h1", "d1"] = "d1"):
    report = read_json("bars.json", None)
    if report is None:
        return {"points": [], "report": None}
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        quality = "within_schedule" if frequency == "h1" else "complete"
        rows = con.execute(f"SELECT bar_end, open, high, low, close, {quality} FROM read_parquet(?) ORDER BY bar_start",
                           [str(root() / report["files"][frequency])]).fetchall()
    return {"points": [{"date": t.isoformat(), "open": o, "high": h, "low": l, "value": c, "complete": q}
                       for t, o, h, l, c, q in rows], "report": report}


@app.get("/api/positioning")
def positioning(limit: int = Query(260, ge=1, le=2000)):
    report = read_json("cftc.json", None)
    if report is None:
        return {"points": [], "report": None}
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        rows = con.execute("SELECT report_date, leveraged_funds_net, asset_manager_net, dealer_net, available_at, time_quality "
                           "FROM read_parquet(?) ORDER BY report_date DESC LIMIT ?",
                           [str(root()/report["files"]["positions"]), limit]).fetchall()
    return {"points": [{"date": str(day), "leveraged": leveraged, "asset_manager": asset, "dealer": dealer,
                        "available_at": available.isoformat() if available else None, "time_quality": quality}
                       for day, leveraged, asset, dealer, available, quality in reversed(rows)], "report": report}


@app.get("/api/policy")
def policy(source: Literal["fomc", "ecb"] = "fomc", limit: int = Query(100, ge=1, le=1000)):
    report = read_json("fomc.json" if source == "fomc" else "ecb_policy.json", None)
    if report is None:
        return {"points": [], "report": None}
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        if source == "fomc":
            rows = con.execute("SELECT decision_date, published_at, target_lower, target_upper, target_midpoint, "
                               "rate_change_bp, available_at FROM read_parquet(?) ORDER BY decision_date DESC LIMIT ?",
                               [str(root()/report["files"]["statements"]), limit]).fetchall()
        else:
            rows = con.execute("SELECT decision_date, published_at, deposit_rate, marginal_lending_rate, deposit_rate, "
                               "deposit_change_bp, available_at FROM read_parquet(?) ORDER BY decision_date DESC LIMIT ?",
                               [str(root()/report["files"]["decisions"]), limit]).fetchall()
    return {"points": [{"date": str(day), "published_at": published.isoformat(), "lower": lower, "upper": upper,
                        "midpoint": midpoint, "change_bp": change, "available_at": available.isoformat() if available else None}
                       for day, published, lower, upper, midpoint, change, available in reversed(rows)],
            "report": report, "source": source}


@app.get("/api/causal-graph")
def causal_graph_api():
    report = read_json("causal_graph.json", None)
    if report is None:
        return {"nodes": [], "links": [], "chains": [], "report": None}
    graph_path = root() / report["files"]["json"]
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["report"] = report
    return graph


@app.get("/reports/market-quality", response_class=HTMLResponse)
def market_quality(request: Request):
    report = read_json("bars.json", None)
    gaps, quarantine = [], []
    if report:
        gaps = json.loads((root()/report["files"]["gaps"]).read_text())
        quarantine = json.loads((root()/report["files"]["quarantine"]).read_text())
    comparison = read_json("ecb_comparison.json", None)
    if not report or not comparison or comparison.get("dataset_id") != report["dataset_id"]:
        comparison = None
    return templates.TemplateResponse(request=request, name="quality.html", context={"bars": report, "gaps": gaps,
                                     "quarantine": quarantine, "comparison": comparison})


@app.get("/reports/macro-data", response_class=HTMLResponse)
def macro_data(request: Request):
    report = read_json("macro_releases.json", None)
    rows = []
    if report and report["files"].get("observations"):
        with duckdb.connect() as con:
            con.execute("SET TimeZone='UTC'")
            rows = con.execute("SELECT observation_period, indicator, actual, unit, published_at, time_quality "
                               "FROM read_parquet(?) ORDER BY published_at DESC, indicator LIMIT 30",
                               [str(root()/report["files"]["observations"])]).fetchall()
    return templates.TemplateResponse(request=request, name="macro.html", context={"macro": report, "rows": rows})


@app.get("/reports/positioning", response_class=HTMLResponse)
def positioning_report(request: Request):
    report = read_json("cftc.json", None)
    rows = []
    if report:
        with duckdb.connect() as con:
            con.execute("SET TimeZone='UTC'")
            rows = con.execute("SELECT report_date, leveraged_funds_net, asset_manager_net, dealer_net, "
                               "available_at, time_quality FROM read_parquet(?) ORDER BY report_date DESC LIMIT 20",
                               [str(root()/report["files"]["positions"])]).fetchall()
    return templates.TemplateResponse(request=request, name="positioning.html", context={"cftc": report, "rows": rows})


@app.get("/reports/policy-events", response_class=HTMLResponse)
def policy_events(request: Request):
    fomc = read_json("fomc.json", None)
    ecb = read_json("ecb_policy.json", None)
    fomc_rows, ecb_rows = [], []
    if fomc:
        with duckdb.connect() as con:
            con.execute("SET TimeZone='UTC'")
            fomc_rows = con.execute("SELECT decision_date, published_at, target_lower, target_upper, rate_change_bp, available_at "
                                    "FROM read_parquet(?) ORDER BY decision_date DESC LIMIT 25",
                                    [str(root()/fomc["files"]["statements"])]).fetchall()
    if ecb:
        with duckdb.connect() as con:
            con.execute("SET TimeZone='UTC'")
            ecb_rows = con.execute("SELECT decision_date, published_at, deposit_rate, mro_rate, marginal_lending_rate, "
                                   "deposit_change_bp, available_at FROM read_parquet(?) ORDER BY decision_date DESC LIMIT 25",
                                   [str(root()/ecb["files"]["decisions"])]).fetchall()
    return templates.TemplateResponse(request=request, name="policy.html",
                                      context={"fomc": fomc, "ecb": ecb, "fomc_rows": fomc_rows, "ecb_rows": ecb_rows})


@app.get("/reports/event-alignment", response_class=HTMLResponse)
def event_alignment(request: Request):
    report = read_json("event_alignment.json", None)
    rows = []
    if report:
        with duckdb.connect() as con:
            con.execute("SET TimeZone='UTC'")
            rows = con.execute("SELECT source, event_type, prediction_mode, prediction_time, target_start_at, "
                               "ret_1d, ret_5d, ret_20d, ret_60d FROM read_parquet(?) "
                               "ORDER BY prediction_time DESC, source LIMIT 30",
                               [str(root()/report["files"]["event_targets"])]).fetchall()
    return templates.TemplateResponse(request=request, name="alignment.html", context={"alignment": report, "rows": rows})


@app.get("/reports/baseline-experiments", response_class=HTMLResponse)
def baseline_experiments(request: Request):
    report = read_json("baseline_experiments.json", None)
    rows = []
    if report:
        with duckdb.connect() as con:
            rows = con.execute("SELECT source, event_type, horizon_sessions, n, mean_return, median_return, hac_se, "
                               "p_value, q_value_bh, overlapping_windows, inference_status FROM read_parquet(?) "
                               "ORDER BY source, event_type, horizon_sessions",
                               [str(root()/report["files"]["results"])]).fetchall()
    return templates.TemplateResponse(request=request, name="experiments.html",
                                      context={"experiments": report, "rows": rows})


@app.get("/reports/causal-graph", response_class=HTMLResponse)
def causal_graph_report(request: Request):
    report = read_json("causal_graph.json", None)
    graph_data = None
    if report:
        graph_data = json.loads((root()/report["files"]["json"]).read_text(encoding="utf-8"))
    return templates.TemplateResponse(request=request, name="graph.html",
                                      context={"graph": report, "graph_data": graph_data})


@app.get("/reports/interaction-experiments", response_class=HTMLResponse)
def interaction_experiments(request: Request):
    report = read_json("interaction_experiments.json", None)
    rows = []
    if report:
        with duckdb.connect() as connection:
            rows = connection.execute(
                "SELECT feature_name,source,event_type,regime,horizon_sessions,n,mean_return,median_return,"
                "positive_share,overlapping_windows,inference_status,sample_warning FROM read_parquet(?) "
                "ORDER BY feature_name,source,event_type,regime,horizon_sessions",
                [str(root()/report["files"]["results"])]).fetchall()
    return templates.TemplateResponse(request=request, name="interactions.html",
                                      context={"interactions": report, "rows": rows})


@app.get("/reports/readiness-review", response_class=HTMLResponse)
def readiness_review(request: Request):
    report = read_json("readiness_review.json", None)
    return templates.TemplateResponse(request=request, name="readiness.html", context={"review": report})


@app.get("/reports/acquisition-review", response_class=HTMLResponse)
def acquisition_review(request: Request):
    report = read_json("acquisition_review.json", None)
    return templates.TemplateResponse(request=request, name="acquisition.html", context={"acquisition": report})


@app.get("/reports/data-coverage", response_class=HTMLResponse)
def data_coverage(request: Request):
    report = read_json("data_coverage.json", None)
    return templates.TemplateResponse(request=request, name="coverage.html", context={"coverage": report})


@app.get("/reports/denn-baseline", response_class=HTMLResponse)
def denn_baseline(request: Request):
    report = read_json("denn_baseline.json", None)
    folds = []
    if report:
        with duckdb.connect() as connection:
            folds = connection.execute(
                "SELECT test_year,horizon_sessions,test_rows,selected_lambda,skill_vs_mean,correlation,sign_accuracy "
                "FROM read_parquet(?) ORDER BY test_year DESC,horizon_sessions LIMIT 24",
                [str(root() / report["files"]["folds"])],
            ).fetchall()
    return templates.TemplateResponse(request=request, name="denn.html", context={"denn": report, "folds": folds})


@app.get("/reports/denn-spectral", response_class=HTMLResponse)
def denn_spectral(request: Request):
    report = read_json("denn_spectral.json", None)
    return templates.TemplateResponse(request=request, name="spectral.html", context={"spectral": report})


@app.get("/reports/denn-spectral-stability", response_class=HTMLResponse)
def denn_spectral_stability(request: Request):
    report = read_json("denn_spectral_stability.json", None)
    return templates.TemplateResponse(request=request, name="spectral_stability.html", context={"stability": report})


@app.get("/reports/denn-timing-audit", response_class=HTMLResponse)
def denn_timing_audit(request: Request):
    report = read_json("denn_timing_audit.json", None)
    rows = []
    if report:
        for name, candidate in report.get("candidates", {}).items():
            naive = candidate.get("naive", {}).get("best", {})
            strict = candidate.get("corrected", {}).get("strict_07", {}).get("best", {})
            rows.append({"name": name, "naive": naive, "strict": strict,
                         "verdict": candidate.get("verdict", "unknown")})
    return templates.TemplateResponse(request=request, name="research_suite.html",
                                      context={"kind": "timing", "report": report, "rows": rows})


@app.get("/reports/denn-state-vector", response_class=HTMLResponse)
def denn_state_vector(request: Request):
    report = read_json("denn_state_vector.json", None)
    rows = []
    if report:
        for horizon in report.get("horizons", []):
            result = report.get("result", {}).get(str(horizon), {})
            importance = result.get("permutation_importance", {})
            rows.append({"horizon": horizon, "baseline": result.get("baseline", {}),
                         "spread_2y": importance.get("spread_2y_z60", {}),
                         "spread_10y": importance.get("spread_10y_z60", {})})
    return templates.TemplateResponse(request=request, name="research_suite.html",
                                      context={"kind": "state", "report": report, "rows": rows})


@app.get("/reports/denn-grouped", response_class=HTMLResponse)
def denn_grouped(request: Request):
    report = read_json("denn_grouped.json", None)
    rows = []
    if report:
        for horizon in report.get("horizons", []):
            result = report.get("result", {}).get(str(horizon), {})
            for block, ablation in result.get("block_ablation", {}).items():
                permutation = result.get("block_permutation_importance", {}).get(block, {})
                rows.append({"horizon": horizon, "block": block, "ablation": ablation,
                             "permutation": permutation})
    return templates.TemplateResponse(request=request, name="research_suite.html",
                                      context={"kind": "grouped", "report": report, "rows": rows})


@app.get("/reports/tier-a", response_class=HTMLResponse)
def tier_a_report(request: Request):
    coverage = read_json("tier_a_coverage.json", None)
    features = read_json("tier_a_features.json", None)
    return templates.TemplateResponse(request=request, name="tier_a.html",
                                      context={"coverage": coverage, "features": features})


@app.get("/reports/{name}", response_class=HTMLResponse)
def document(request: Request, name: str):
    if name not in DOCS:
        raise HTTPException(404)
    path = PROJECT / DOCS[name]
    content = markdown.markdown(path.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    return templates.TemplateResponse(request=request, name="document.html", context={"content": content})


@app.get("/download/{name}")
def download(name: str):
    files = {"market.json": root() / "reports" / "market.json", "sources.json": root() / "reports" / "sources.json",
             "ecb_eurusd.parquet": root() / "silver" / "ecb_eurusd.parquet",
             "bars.json": root()/"reports"/"bars.json", "ecb_comparison.json": root()/"reports"/"ecb_comparison.json",
             "source_audit.json": root()/"reports"/"source_audit.json"}
    report = read_json("bars.json", None)
    if report:
        files.update({"eurusd_h1.parquet": root()/report["files"]["h1"], "eurusd_d1.parquet": root()/report["files"]["d1"],
                      "gaps.json": root()/report["files"]["gaps"], "quarantine.json": root()/report["files"]["quarantine"]})
    macro = read_json("macro_releases.json", None)
    if macro:
        files["macro_releases.json"] = root()/"reports"/"macro_releases.json"
        files["macro_releases.parquet"] = root()/macro["files"]["releases"]
        if macro["files"].get("observations"):
            files["macro_observations.parquet"] = root()/macro["files"]["observations"]
    cftc = read_json("cftc.json", None)
    if cftc:
        files["cftc.json"] = root()/"reports"/"cftc.json"
        files["cftc_eur_tff.parquet"] = root()/cftc["files"]["positions"]
    fomc = read_json("fomc.json", None)
    if fomc:
        files["fomc.json"] = root()/"reports"/"fomc.json"
        files["fomc_statements.parquet"] = root()/fomc["files"]["statements"]
    ecb_policy = read_json("ecb_policy.json", None)
    if ecb_policy:
        files["ecb_policy.json"] = root()/"reports"/"ecb_policy.json"
        files["ecb_policy_decisions.parquet"] = root()/ecb_policy["files"]["decisions"]
    alignment = read_json("event_alignment.json", None)
    if alignment:
        files["event_alignment.json"] = root()/"reports"/"event_alignment.json"
        files["event_targets.parquet"] = root()/alignment["files"]["event_targets"]
    experiments = read_json("baseline_experiments.json", None)
    if experiments:
        files["baseline_experiments.json"] = root()/"reports"/"baseline_experiments.json"
        files["baseline_results.parquet"] = root()/experiments["files"]["results"]
    graph = read_json("causal_graph.json", None)
    if graph:
        files["causal_graph.json"] = root()/graph["files"]["json"]
        files["causal_graph.graphml"] = root()/graph["files"]["graphml"]
        files["causal_graph_manifest.json"] = root()/"reports"/"causal_graph.json"
    interactions = read_json("interaction_experiments.json", None)
    if interactions:
        files["interaction_experiments.json"] = root()/"reports"/"interaction_experiments.json"
        files["interaction_features.parquet"] = root()/interactions["files"]["features"]
        files["interaction_results.parquet"] = root()/interactions["files"]["results"]
    readiness = read_json("readiness_review.json", None)
    if readiness:
        files["readiness_review.json"] = root()/"reports"/"readiness_review.json"
    acquisition = read_json("acquisition_review.json", None)
    if acquisition:
        files["acquisition_review.json"] = root()/"reports"/"acquisition_review.json"
        files["eurusd_m1_sample.parquet"] = root()/acquisition["files"]["m1_sample"]
    coverage = read_json("data_coverage.json", None)
    if coverage:
        files["data_coverage.json"] = root()/"reports"/"data_coverage.json"
        files["common_d1.parquet"] = root()/coverage["files"]["common_d1"]
        for series_id, relative in coverage["files"].items():
            if series_id != "common_d1":
                files[f"{series_id.lower()}.parquet"] = root()/relative
    denn = read_json("denn_baseline.json", None)
    if denn:
        files["denn_baseline.json"] = root()/"reports"/"denn_baseline.json"
        for label, relative in denn["files"].items():
            files[f"denn_{label}.parquet"] = root()/relative
    spectral = read_json("denn_spectral.json", None)
    if spectral:
        files["denn_spectral.json"] = root()/"reports"/"denn_spectral.json"
        for label, relative in spectral["files"].items():
            files[f"spectral_{label}.parquet"] = root()/relative
    stability = read_json("denn_spectral_stability.json", None)
    if stability:
        files["denn_spectral_stability.json"] = root()/"reports"/"denn_spectral_stability.json"
        for label, relative in stability["files"].items():
            files[f"spectral_stability_{label}.parquet"] = root()/relative
    for report_name in ("denn_timing_audit", "denn_state_vector", "denn_grouped",
                        "tier_a_coverage", "tier_a_features"):
        report = read_json(f"{report_name}.json", None)
        if report:
            files[f"{report_name}.json"] = root()/"reports"/f"{report_name}.json"
            for label, relative in report.get("files", {}).items():
                suffix = Path(relative).suffix or ".parquet"
                files[f"{report_name}_{str(label).lower()}{suffix}"] = root()/relative
    if name not in files or not files[name].exists():
        raise HTTPException(404)
    return FileResponse(files[name], filename=name)
