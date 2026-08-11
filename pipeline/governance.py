#!/usr/bin/env python3
"""
Phase 1: Governance Adjudication — add validation layer to Fact nodes.

Adds governance_status, governance_checked_at, and governance_notes
to Fact nodes. Provides validation functions and HITL review queue.

Usage:
    python3 governance.py init              # Add governance fields to existing Facts
    python3 governance.py validate           # Validate all ungoverned Facts
    python3 governance.py review             # List facts pending HITL review
    python3 governance.py approve <uid>      # Approve a pending fact
    python3 governance.py reject <uid>       # Reject a pending fact with reason
    python3 governance.py stats              # Show governance statistics
"""
import sys, json, argparse, datetime, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE
from neo4j import GraphDatabase
from openai import OpenAI

log = print

# ── Governance statuses ───────────────────────────────────────────────
PENDING = "PENDING"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
CONFLICT = "CONFLICT"


def add_governance_fields(n4j):
    """Add governance fields to existing Fact nodes."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (f:Fact)
            WHERE f.governance_status IS NULL
            SET f.governance_status = $default,
                f.governance_checked_at = datetime(),
                f.governance_notes = ""
            RETURN count(f) AS updated
        """, default=PENDING)
        print(f"✅ Added governance fields to {r.single()['updated']} Fact nodes")


def validate_fact(llm, fact_text, existing_facts):
    """Validate a single fact for fidelity, currency, and conflict."""
    prompt = f"""Validate this fact for governance adjudication.

Fact: "{fact_text}"

Check three dimensions:
1. FIDELITY: Is the fact internally consistent and well-formed?
2. CURRENCY: Does the fact contain time-sensitive information that may be stale?
3. CONFLICT: Does this fact contradict any of these existing facts?
   Existing facts: {json.dumps(existing_facts[:5])}

Return JSON:
{{"fidelity": true/false, "fidelity_reason": "...",
  "currency": true/false, "currency_reason": "...",
  "conflict": true/false, "conflict_reason": "...",
  "governance_status": "APPROVED|PENDING|REJECTED|CONFLICT",
  "overall_reason": "..."
}}

Rules:
- APPROVED: passes all three checks
- PENDING: minor concerns but no clear violation
- REJECTED: clear fidelity or currency failure
- CONFLICT: directly contradicts existing knowledge
"""
    try:
        resp = llm.chat.completions.create(
            model="gemma4:e4b-it-qat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=512
        )
        content = resp.choices[0].message.content
        content = re.sub(r'```(?:json)?\s*', '', content).strip()
        s, e = content.find('{'), content.rfind('}')
        if s >= 0 and e > s:
            return json.loads(content[s:e+1])
    except Exception as ex:
        log(f"  ⚠️ Validation failed: {ex}")
    return {"governance_status": PENDING, "overall_reason": "Validation error"}


def run_validation(n4j, llm):
    """Run governance validation on all pending Facts."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (f:Fact)
            WHERE f.governance_status = $status
            RETURN f.uid, f.text
            LIMIT 50
        """, status=PENDING)
        facts = [(r["f.uid"], r["f.text"]) for r in r]
    print(f"🔍 Validating {len(facts)} pending facts")

    for uid, text in facts:
        # Get conflicting facts
        with n4j.session() as s:
            existing = [r["f.text"] for r in s.run(
                "MATCH (f:Fact) WHERE f.uid <> $u AND f.governance_status = $s RETURN f.text LIMIT 5",
                u=uid, s=APPROVED)]
        result = validate_fact(llm, text, existing)
        status = result.get("governance_status", PENDING)
        reason = result.get("overall_reason", "")
        with n4j.session() as s:
            s.run("""
                MATCH (f:Fact {uid: $u})
                SET f.governance_status = $s,
                    f.governance_checked_at = datetime(),
                    f.governance_notes = $r
            """, u=uid, s=status, r=reason)
        log(f"  {'✅' if status == APPROVED else '❌'} {(text or '?')[:60]} → {status}")

    print(f"✅ Validation complete")


def list_pending(n4j):
    """List facts pending HITL review."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (f:Fact)
            WHERE f.governance_status IN [$p, $c]
            RETURN f.uid, f.text, f.governance_status, f.governance_notes
            ORDER BY f.governance_checked_at DESC
            LIMIT 30
        """, p=PENDING, c=CONFLICT)
        facts = [(r["f.uid"], r["f.text"][:80], r["f.governance_status"], r["f.governance_notes"][:60]) for r in r]
    if facts:
        print(f"📋 {len(facts)} facts pending HITL review:")
        for uid, text, status, notes in facts:
            print(f"  {uid} | {status:8s} | {text}")
            if notes:
                print(f"         Notes: {notes}")
    else:
        print("✅ No facts pending review")


def approve_fact(n4j, uid, notes=""):
    """Manually approve a pending fact."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (f:Fact {uid: $u})
            SET f.governance_status = $s,
                f.governance_checked_at = datetime(),
                f.governance_notes = $n
            RETURN f.text
        """, u=uid, s=APPROVED, n=notes)
        row = r.single()
        if row:
            print(f"✅ Approved: {row['f.text'][:60]}")
        else:
            print(f"❌ Fact not found: {uid}")


def reject_fact(n4j, uid, reason="Rejected by HITL"):
    """Reject a pending fact."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (f:Fact {uid: $u})
            SET f.governance_status = $s,
                f.governance_checked_at = datetime(),
                f.governance_notes = $n
            RETURN f.text
        """, u=uid, s=REJECTED, n=reason)
        row = r.single()
        if row:
            print(f"❌ Rejected: {row['f.text'][:60]}")
        else:
            print(f"❌ Fact not found: {uid}")


def show_stats(n4j):
    """Show governance statistics."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (f:Fact)
            RETURN f.governance_status, count(f) AS cnt
            ORDER BY cnt DESC
        """)
        print("📊 Governance Statistics:")
        total = 0
        for row in r:
            print(f"  {row['f.governance_status'] or 'NONE':15s} {row['cnt']:5d}")
            total += row['cnt']
        print(f"  {'TOTAL':15s} {total:5d}")

        # Facts by governance_status
        r = s.run("""
            MATCH (f:Fact) 
            WHERE f.governance_status IS NOT NULL
            WITH f.governance_status AS status, count(f) AS cnt
            RETURN status, cnt ORDER BY cnt DESC
        """)
        # Show as percentage
        for row in r:
            pct = row['cnt'] / total * 100 if total > 0 else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            print(f"  {row['status']:10s} | {bar} | {row['cnt']:5d} ({pct:.0f}%)")


def main():
    p = argparse.ArgumentParser(description="Governance Adjudication")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Add governance fields to existing Facts")
    sub.add_parser("validate", help="Validate all pending Facts")
    sub.add_parser("review", help="List facts pending HITL review")

    ap = sub.add_parser("approve", help="Approve a pending fact")
    ap.add_argument("uid", help="Fact UID to approve")
    ap.add_argument("--notes", "-n", default="Approved by HITL", help="Approval notes")

    rp = sub.add_parser("reject", help="Reject a pending fact")
    rp.add_argument("uid", help="Fact UID to reject")
    rp.add_argument("--reason", "-r", default="Rejected by HITL", help="Rejection reason")

    sub.add_parser("stats", help="Show governance statistics")

    args = p.parse_args()
    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")

    if args.command == "init":
        add_governance_fields(n4j)
    elif args.command == "validate":
        run_validation(n4j, llm)
    elif args.command == "review":
        list_pending(n4j)
    elif args.command == "approve":
        approve_fact(n4j, args.uid, args.notes)
    elif args.command == "reject":
        reject_fact(n4j, args.uid, args.reason)
    elif args.command == "stats":
        show_stats(n4j)

    n4j.close()


if __name__ == "__main__":
    main()