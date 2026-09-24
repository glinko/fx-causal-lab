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
DOCS = {"decisions": "DECISIONS.md", "questions": "OPEN_QUESTIONS.md", "sources": "SOURCE_MATRIX.md", "deployment": "DEPLOYMENT.md"}


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
    if name not in files or not files[name].exists():
        raise HTTPException(404)
    return FileResponse(files[name], filename=name)
