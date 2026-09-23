import json
import os
from pathlib import Path

import duckdb
import markdown
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .recon import CONFIG
from .store import root

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
    return {"status": "ok", "version": "0.1.0"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={
        "market": read_json("market.json", None),
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
             "ecb_eurusd.parquet": root() / "silver" / "ecb_eurusd.parquet"}
    if name not in files or not files[name].exists():
        raise HTTPException(404)
    return FileResponse(files[name], filename=name)
