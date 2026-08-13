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
    bc_p = sub.add_parser("build-concepts", help="Build concept hierarchy from entities (AutoSchemaKG-inspired)")
    bc_p.add_argument("--dry-run", action="store_true")
    bc_p.add_argument("--limit", type=int, default=200)
    bc_p.add_argument("--entity-type")
    ee_p = sub.add_parser("extract-events", help="Extract events from chunks/news (AutoSchemaKG EV+VV)")
    ee_p.add_argument("--limit", type=int, default=50)
    ee_p.add_argument("--doc", help="Process only one doc's chunks")
    ee_p.add_argument("--dry-run", action="store_true")
    ee_p.add_argument("--model", choices=["gemma4", "deepseek", "nemotron"], default="gemma4")
    gs_p = sub.add_parser("graphrag-search", help="Semantic search + graph traversal (RAG)")
    gs_p.add_argument("query", help="Search query")
    gs_p.add_argument("--top-k", type=int, default=10)
    gs_p.add_argument("--hops", type=int, default=3)
    ce_p = sub.add_parser("classify-events", help="Classify events by topic (Option B+C)")
    ce_p.add_argument("action", choices=["classify", "seed", "topics", "review", "approve", "reject"],
                      nargs="?", default="classify", help="Action to perform")
    ce_p.add_argument("--dry-run", action="store_true")
    ce_p.add_argument("--model", choices=["gemma4", "deepseek"], default="deepseek")
    ce_p.add_argument("--batch-size", type=int, default=10)
    ce_p.add_argument("topic", nargs="?", help="Topic name for approve/reject")
    hr_p = sub.add_parser("hipporag", help="HippoRAG2-style personalized PageRank retrieval")
    hr_p.add_argument("query", help="Search query")
    hr_p.add_argument("--top-k", type=int, default=10)
    hr_p.add_argument("--hops", type=int, default=3)

    # ── governance ────────────────────────────────────────────────────────
    gov_p = sub.add_parser("governance", help="Governance Adjudication (Phase 1)")
    gov_p.add_argument("action", choices=["init", "validate", "review", "approve", "reject", "stats"])
    gov_p.add_argument("--uid", help="Fact UID (for approve/reject)")
    gov_p.add_argument("--notes", "-n", default="", help="Notes for approve/reject")
    gov_p.add_argument("--reason", "-r", default="", help="Reason for reject")

    # ── meta-kb ────────────────────────────────────────────────────────────
    mk_p = sub.add_parser("meta-kb", help="Meta-Knowledge Base (Phase 2) — ontology versioning & schema evolution")
    mk_p.add_argument("action", choices=[
        "init", "snapshot", "history", "diff",
        "propose", "review", "show", "approve", "reject",
        "analyze", "schema-history",
    ])
    mk_p.add_argument("--uid", help="Proposal UID (for approve/reject/show)")
    mk_p.add_argument("--v1", help="First version for diff")
    mk_p.add_argument("--v2", help="Second version for diff")
    mk_p.add_argument("--file", "-f", help="JSON file with proposed changes")
    mk_p.add_argument("--source", "-s", default=None,
                      choices=["manual", "pipeline", "auto-extract"],
                      help="Source of the proposal")
    mk_p.add_argument("--analyze", action="store_true",
                      help="Auto-analyze extraction patterns")
    mk_p.add_argument("--apply", action="store_true",
                      help="Auto-approve (skip HITL)")
    mk_p.add_argument("--reviewer", default=None, help="Reviewer name")
    mk_p.add_argument("--notes", "-n", default=None, help="Review notes")
    mk_p.add_argument("--reason", default=None, help="Rejection reason")
    mk_p.add_argument("--description", "-d", default=None, help="Version description")
    mk_p.add_argument("--limit", type=int, default=None, help="Max results")
    mk_p.add_argument("--auto-propose", action="store_true",
                      help="Auto-create proposals from analysis")

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
        "build-concepts": "build_concepts.py",
        "extract-events": "extract_events.py",
        "graphrag-search": "graphrag_search.py",
        "hipporag": "hipporag_retrieve.py",
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
    elif args.command == "meta-kb":
        script_args = [args.action]
        if args.uid:
            script_args.extend(["--uid", args.uid])
        if args.v1 and args.v2:
            script_args.extend([args.v1, args.v2])
        if args.file:
            script_args.extend(["--file", args.file])
        if args.source:
            script_args.extend(["--source", args.source])
        if args.analyze:
            script_args.append("--analyze")
        if args.apply:
            script_args.append("--apply")
        if args.reviewer:
            script_args.extend(["--reviewer", args.reviewer])
        if args.notes:
            script_args.extend(["--notes", args.notes])
        if args.reason:
            script_args.extend(["--reason", args.reason])
        if args.description:
            script_args.extend(["--description", args.description])
        if args.limit:
            script_args.extend(["--limit", str(args.limit)])
        if args.auto_propose:
            script_args.append("--auto-propose")
        run_script("meta_kb.py", script_args)
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
            # Positional 'query' argument for graphrag-search and hipporag
            if key == "query":
                script_args.append(str(val))
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