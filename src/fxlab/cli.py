import argparse
import json
from datetime import date, datetime
from pathlib import Path

from .recon import run_recon


def main():
    parser = argparse.ArgumentParser(description="FX Causal Lab: reproducible EUR/USD research")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("recon", help="Probe configured public endpoints and preserve responses")
    backfill = commands.add_parser("backfill", help="Download ECB reference series; not trading bars")
    backfill.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 23))
    backfill.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 9, 23))
    bars = commands.add_parser("market-backfill", help="Dukascopy H1, NY17 D1 and quality report; no synthetic bars")
    bars.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 23))
    bars.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 9, 23))
    bars.add_argument("--offline", action="store_true", help="Use preserved raw snapshots only")
    bars.add_argument("--cutoff", type=datetime.fromisoformat, help="Freeze an aware timestamp for replay")
    market_replay = commands.add_parser("market-replay", help="Replay exact manifest snapshots offline")
    market_replay.add_argument("manifest", type=Path)
    commands.add_parser("market-check", help="Compare broker H1 ranges with daily ECB reference")
    for name, help_text in [("macro-backfill", "Fetch and normalize BLS archives on one host"),
                            ("macro-fetch", "Fetch BLS archives for transfer to Ubuntu")]:
        macro = commands.add_parser(name, help=help_text)
        macro.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 1))
        macro.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today())
    macro_replay = commands.add_parser("macro-replay", help="Normalize a preserved BLS fetch manifest without network")
    macro_replay.add_argument("manifest", type=Path)
    cftc = commands.add_parser("cftc-backfill", help="Fetch and normalize CFTC EUR TFF Futures Only positioning")
    cftc.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 1))
    cftc.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today())
    cftc_replay = commands.add_parser("cftc-replay", help="Normalize preserved CFTC snapshots without network")
    cftc_replay.add_argument("manifest", type=Path)
    fomc = commands.add_parser("fomc-backfill", help="Fetch and normalize archived FOMC statements")
    fomc.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 1))
    fomc.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today())
    fomc_replay = commands.add_parser("fomc-replay", help="Normalize preserved FOMC snapshots without network")
    fomc_replay.add_argument("manifest", type=Path)
    ecb_policy = commands.add_parser("ecb-policy-backfill", help="Fetch and normalize ECB monetary-policy decisions")
    ecb_policy.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 1))
    ecb_policy.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today())
    ecb_policy_replay = commands.add_parser("ecb-policy-replay", help="Normalize preserved ECB policy snapshots offline")
    ecb_policy_replay.add_argument("manifest", type=Path)
    commands.add_parser("align-events", help="Build leakage-aware EUR/USD targets for preserved events")
    commands.add_parser("baseline-experiments", help="Build descriptive M5 event baselines and replication status")
    commands.add_parser("graph-build", help="Validate and export the M6 causal-hypothesis graph")
    commands.add_parser("interaction-experiments", help="Build leakage-aware M7 descriptive regime slices")
    replay = commands.add_parser("replay", help="Normalize preserved ECB snapshot offline")
    replay.add_argument("metadata", type=Path)
    replay.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2023, 9, 23))
    replay.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 9, 23))
    args = parser.parse_args()
    if args.command == "recon":
        result = run_recon()
    elif args.command == "backfill":
        from .providers import ECBReferenceProvider
        result = ECBReferenceProvider().backfill(args.start, args.end)
    elif args.command == "market-backfill":
        from .market import backfill_bars
        result = backfill_bars(args.start, args.end, offline=args.offline, cutoff=args.cutoff)
    elif args.command == "market-replay":
        from .market import replay_bars
        result = replay_bars(args.manifest)
    elif args.command == "market-check":
        from .quality import ecb_comparison
        from .store import root
        result = ecb_comparison(json.loads((root()/"reports"/"bars.json").read_text()))
    elif args.command == "macro-backfill":
        from .macro import backfill_bls_releases
        result = backfill_bls_releases(args.start, args.end)
    elif args.command == "macro-fetch":
        from .macro import fetch_bls_snapshots
        result = fetch_bls_snapshots(args.start, args.end)
    elif args.command == "macro-replay":
        from .macro import replay_bls_releases
        result = replay_bls_releases(args.manifest)
    elif args.command == "cftc-backfill":
        from .cftc import backfill_cftc
        result = backfill_cftc(args.start, args.end)
    elif args.command == "cftc-replay":
        from .cftc import replay_cftc
        result = replay_cftc(args.manifest)
    elif args.command == "fomc-backfill":
        from .fomc import backfill_fomc
        result = backfill_fomc(args.start, args.end)
    elif args.command == "fomc-replay":
        from .fomc import replay_fomc
        result = replay_fomc(args.manifest)
    elif args.command == "ecb-policy-backfill":
        from .ecb_policy import backfill_ecb_policy
        result = backfill_ecb_policy(args.start, args.end)
    elif args.command == "ecb-policy-replay":
        from .ecb_policy import replay_ecb_policy
        result = replay_ecb_policy(args.manifest)
    elif args.command == "align-events":
        from .alignment import build_event_targets
        result = build_event_targets()
    elif args.command == "baseline-experiments":
        from .experiments import build_baseline_experiments
        result = build_baseline_experiments()
    elif args.command == "graph-build":
        from .causal_graph import build_causal_graph
        result = build_causal_graph()
    elif args.command == "interaction-experiments":
        from .interactions import build_interaction_experiments
        result = build_interaction_experiments()
    else:
        from .providers import ECBReferenceProvider
        from .store import root
        meta = json.loads(args.metadata.read_text(encoding="utf-8"))
        body = (root() / meta["payload"]).read_bytes()
        result = ECBReferenceProvider().replay(body, meta, args.start, args.end)
    print(json.dumps(result, ensure_ascii=False, indent=2))
