import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def root() -> Path:
    return Path(os.environ.get("FXLAB_DATA", "data"))


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def save_raw(source: str, url: str, body: bytes, headers: dict, status_code: int = 200) -> dict:
    digest = hashlib.sha256(body).hexdigest()
    directory = root() / "bronze" / source
    directory.mkdir(parents=True, exist_ok=True)
    payload = directory / f"{digest}.payload"
    if not payload.exists():
        with payload.open("xb") as target:
            target.write(body)
    meta = {"source": source, "source_url": url, "sha256": digest,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "headers": {k: v for k, v in headers.items() if k.lower() in {"content-type", "last-modified", "etag", "date"}},
            "status_code": status_code, "parser_version": "0.1.0", "payload": str(payload.relative_to(root()))}
    atomic_json(directory / f"{digest}.{uuid4().hex}.json", meta)
    return meta
