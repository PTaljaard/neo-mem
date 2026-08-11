#!/usr/bin/env python3
"""
neo-mem pipeline runner — single entry point for all pipeline operations.

Usage:
    python3 run.py news --max-articles 6
    python3 run.py news --claims 5
    python3 run.py monitor "Frans Cronje" --days 14
    python3 run.py ingest /path/to/paper.pdf
    python3 run.py extract-entities doc-uid
    python3 run.py batch /srv/kb/pieter/Incoming/
    python3 run.py migrate-claims          # one-time migration
    python3 run.py add-confidence-schema   # one-time schema update
    python3 run.py verify-claims
    python3 run.py verify-commentators
    python3 run.py list-tasks              # show kanban tasks

Config is read from pipeline_config.py, which reads from environment variables.
Override any setting: NEO4J_URI=bolt://... python3 run.py news
"""
import sys, os, subprocess, argparse
from pathlib import Path

# Ensure pipeline/ is on the Python path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from pipeline_config import DEFAULT_CLAIM_COUNT, DEFAULT_MAX_ARTICLES, DEFAULT_COMMENTATOR_DAYS


def run_script(script_name, args):
    """Run a pipeline script with the given arguments."""
    script_path = HERE / script_name
    if not script_path.exists():
        print(f"❌ Script not found: {script_path}")
        sys.exit(1)
    cmd = [sys.executable, str(script_path)] + args
    print(f"🚀 Running: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def main():
    p = argparse.ArgumentParser(description="neo-mem pipeline runner")
    sub = p.add_subparsers(dest="command", required=True)

    # ── news ────────────────────────────────────────────────────────────
    news_p = sub.add_parser("news", help="Fetch and ingest news articles")
    news_p.add_argument("--max-articles", type=int, default=DEFAULT_MAX_ARTICLES)
    news_p.add_argument("--claims", type=int, default=DEFAULT_CLAIM_COUNT)
    news_p.add_argument("--no-firecrawl", action="store_true")

    # ── monitor ─────────────────────────────────────────────────────────
    mon_p = sub.add_parser("monitor", help="Monitor a commentator's commentary")
    mon_p.add_argument("person", help="Name of the person to monitor")
    mon_p.add_argument("--days", type=int, default=DEFAULT_COMMENTATOR_DAYS)
    mon_p.add_argument("--claims", type=int, default=DEFAULT_CLAIM_COUNT)
    mon_p.add_argument("--dry-run", action="store_true")

    # ── ingest ──────────────────────────────────────────────────────────
    ing_p = sub.add_parser("ingest", help="Ingest a PDF document")
    ing_p.add_argument("path", help="Path to the PDF file")

    # ── extract-entities ────────────────────────────────────────────────
    ext_p = sub.add_parser("extract-entities", help="Extract entities from a document's chunks")
    ext_p.add_argument("doc_uid", help="Document UID in Neo4j")

    # ── batch ───────────────────────────────────────────────────────────
    batch_p = sub.add_parser("batch", help="Batch ingest all PDFs in a directory")
    batch_p.add_argument("directory", help="Directory containing PDFs")

    # ── one-time scripts ────────────────────────────────────────────────
    sub.add_parser("migrate-claims", help="Migrate claim arrays to Claim nodes (one-time)")
    sub.add_parser("add-confidence-schema", help="Add confidence dimensions to Fact nodes (one-time)")
    sub.add_parser("verify-claims", help="Verify Claim node structure")
    sub.add_parser("verify-commentators", help="Verify commentator graph")
    sub.add_parser("list-tasks", help="List kanban tasks for the PhD pipeline")

    # ── governance ────────────────────────────────────────────────────────
    gov_p = sub.add_parser("governance", help="Governance Adjudication (Phase 1)")
    gov_p.add_argument("action", choices=["init", "validate", "review", "approve", "reject", "stats"])
    gov_p.add_argument("--uid", help="Fact UID (for approve/reject)")
    gov_p.add_argument("--notes", "-n", default="", help="Notes for approve/reject")
    gov_p.add_argument("--reason", "-r", default="", help="Reason for reject")
    sub.add_parser("help", help="Show this help")

    args = p.parse_args()

    # Map commands to scripts
    script_map = {
        "news": "news_ingester.py",
        "monitor": "person_monitor.py",
        "ingest": "ingest_document.py",
        "extract-entities": "extract_entities.py",
        "batch": "batch_ingest.py",
        "migrate-claims": "migrate_claims_to_nodes.py",
        "add-confidence-schema": "add_confidence_schema.py",
        "verify-claims": "verify_claims.py",
        "verify-commentators": "verify_commentators.py",
    }

    if args.command == "governance":
        script_args = [args.action]
        if args.uid:
            script_args.extend(["--uid", args.uid])
        if args.notes:
            script_args.extend(["--notes", args.notes])
        if args.reason:
            script_args.extend(["--reason", args.reason])
        run_script("governance.py", script_args)
    elif args.command == "help":
        p.print_help()
        sys.exit(0)

    if args.command == "list-tasks":
        os.system("hermes kanban boards switch phd-pipeline 2>/dev/null; hermes kanban list")
        sys.exit(0)

    if args.command in script_map:
        # Build the CLI args for the target script
        script_args = []
        for key, val in vars(args).items():
            if key == "command":
                continue
            if val is None or val is False:
                continue
            flag = f"--{key.replace('_', '-')}"
            if val is True:
                script_args.append(flag)
            else:
                script_args.extend([flag, str(val)])
        run_script(script_map[args.command], script_args)
    else:
        p.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()