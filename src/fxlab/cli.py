import argparse
import json
from datetime import date
from pathlib import Path

from .providers import ECBReferenceProvider
from .recon import run_recon


def main():
    parser = argparse.ArgumentParser(description="FX Causal Lab: reproducible EUR/USD research")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("recon", help="Probe configured public endpoints and preserve responses")
    backfill = commands.add_parser("backfill", help="Download ECB reference series; not trading bars")
    backfill.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 23))
    backfill.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 9, 23))
    replay = commands.add_parser("replay", help="Normalize preserved ECB snapshot offline")
    replay.add_argument("metadata", type=Path)
    replay.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 23))
    replay.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 9, 23))
    args = parser.parse_args()
    if args.command == "recon":
        result = run_recon()
    elif args.command == "backfill":
        result = ECBReferenceProvider().backfill(args.start, args.end)
    else:
        from .store import root
        meta = json.loads(args.metadata.read_text(encoding="utf-8"))
        body = (root() / meta["payload"]).read_bytes()
        result = ECBReferenceProvider().replay(body, meta, args.start, args.end)
    print(json.dumps(result, ensure_ascii=False, indent=2))
