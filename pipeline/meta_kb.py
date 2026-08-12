#!/usr/bin/env python3
"""
Phase 2: Meta-Knowledge Base — ontology versioning & schema evolution.

The MKB tracks ontology evolution over time — every class/property addition,
modification, or removal is recorded as a SchemaChange, grouped into
OntologyVersions, and proposals from extraction patterns go through a HITL
approval workflow before being applied.

Usage:
    python3 meta_kb.py init                    # Create v1.0 snapshot from current ontology
    python3 meta_kb.py snapshot                # Capture current ontology as new version
    python3 meta_kb.py history                 # Show version history
    python3 meta_kb.py diff v1.0 v1.1          # Diff two versions
    python3 meta_kb.py propose [--analyze]     # Propose schema changes (from analysis or stdin)
    python3 meta_kb.py review                  # List proposals pending HITL review
    python3 meta_kb.py approve <proposal_uid>  # Approve & apply a proposal
    python3 meta_kb.py reject <proposal_uid>   # Reject a proposal
    python3 meta_kb.py analyze                 # Scan extraction patterns for schema candidates
"""
import sys, json, argparse, datetime, uuid, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE, CHAT_MODEL
from neo4j import GraphDatabase

log = print

# ── SchemaProposal statuses ─────────────────────────────────────────
STATUS_DRAFT = "DRAFT"
STATUS_REVIEW = "UNDER_REVIEW"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_IMPLEMENTED = "IMPLEMENTED"

# ── SchemaChange types ──────────────────────────────────────────────
CHANGE_ADD_CLASS = "ADD_CLASS"
CHANGE_REMOVE_CLASS = "REMOVE_CLASS"
CHANGE_MODIFY_CLASS = "MODIFY_CLASS"
CHANGE_ADD_PROP = "ADD_PROPERTY"
CHANGE_REMOVE_PROP = "REMOVE_PROPERTY"
CHANGE_MODIFY_PROP = "MODIFY_PROPERTY"
CHANGE_ADD_REL = "ADD_RELATIONSHIP"
CHANGE_MODIFY_REL = "MODIFY_RELATIONSHIP"
VALID_CHANGE_TYPES = {
    CHANGE_ADD_CLASS, CHANGE_REMOVE_CLASS, CHANGE_MODIFY_CLASS,
    CHANGE_ADD_PROP, CHANGE_REMOVE_PROP, CHANGE_MODIFY_PROP,
    CHANGE_ADD_REL, CHANGE_MODIFY_REL,
}


# ═══════════════════════════════════════════════════════════════════
#  ONTOLOGY SNAPSHOT
# ═══════════════════════════════════════════════════════════════════

def make_uid(prefix="sc"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def get_current_ontology(n4j):
    """Fetch all OntologyClass and Property nodes as a serialisable dict."""
    with n4j.session() as s:
        classes = list(s.run("""
            MATCH (c:OntologyClass)
            RETURN c.class_id AS class_id,
                   c.label AS label,
                   c.description AS description,
                   c.extends AS extends,
                   c.properties AS properties
            ORDER BY c.class_id
        """))
        props = list(s.run("""
            MATCH (p:Property)
            RETURN p.key AS key,
                   p.description AS description,
                   p.datatype AS datatype
            ORDER BY p.key
        """))
    return {
        "classes": [dict(r) for r in classes],
        "properties": [dict(r) for r in props],
        "class_count": len(classes),
        "property_count": len(props),
    }


def create_version_snapshot(n4j, description="", auto=False):
    """Snapshot current ontology as a new OntologyVersion."""
    ontology = get_current_ontology(n4j)
    version_str = _next_version(n4j)
    snapshot_json = json.dumps(ontology, default=str, indent=2)

    with n4j.session() as s:
        s.run("""
            MERGE (v:OntologyVersion {version: $version})
            SET v.created_at = datetime(),
                v.description = $desc,
                v.class_count = $cc,
                v.property_count = $pc,
                v.snapshot = $snap,
                v.source = $src
        """, version=version_str, desc=description,
             cc=ontology["class_count"], pc=ontology["property_count"],
             snap=snapshot_json, src="auto" if auto else "manual")
    log(f"📸 Created OntologyVersion {version_str} "
        f"({ontology['class_count']} classes, {ontology['property_count']} properties)")
    return version_str


def _next_version(n4j):
    """Determine the next version string (v1.0 → v1.1 → v2.0 for minor/major)."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (v:OntologyVersion)
            RETURN v.version AS ver
            ORDER BY v.created_at DESC
            LIMIT 1
        """).single()
    if not r:
        return "v1.0"
    current = r["ver"]
    match = re.match(r"v(\d+)\.(\d+)", current)
    if not match:
        return "v1.1"
    major, minor = int(match.group(1)), int(match.group(2))
    return f"v{major}.{minor + 1}"


def show_history(n4j):
    """Display version history."""
    with n4j.session() as s:
        results = list(s.run("""
            MATCH (v:OntologyVersion)
            RETURN v.version AS version,
                   v.created_at AS created_at,
                   v.description AS description,
                   v.class_count AS classes,
                   v.property_count AS properties,
                   v.source AS source
            ORDER BY v.created_at ASC
        """))
    if not results:
        log("📭 No ontology versions recorded yet. Run `init` or `snapshot`.")
        return
    log(f"{'Version':12s} {'Date':24s} {'Classes':8s} {'Props':6s} {'Source':10s} Description")
    log("-" * 90)
    for r in results:
        dt = r["created_at"].isoformat()[:19] if r["created_at"] else "?"
        src = r["source"] or "manual"
        desc = (r["description"] or "")[:40]
        log(f"{r['version']:12s} {dt:24s} {r['classes']:8d} {r['properties']:6d} {src:10s} {desc}")


def diff_versions(n4j, v1, v2):
    """Show what changed between two OntologyVersion snapshots."""
    with n4j.session() as s:
        snap1 = s.run("MATCH (v:OntologyVersion {version: $v}) RETURN v.snapshot AS snap",
                       v=v1).single()
        snap2 = s.run("MATCH (v:OntologyVersion {version: $v}) RETURN v.snapshot AS snap",
                       v=v2).single()
    if not snap1:
        log(f"❌ Version {v1} not found")
        return
    if not snap2:
        log(f"❌ Version {v2} not found")
        return

    o1 = json.loads(snap1["snap"])
    o2 = json.loads(snap2["snap"])

    c1 = {c["class_id"]: c for c in o1["classes"]}
    c2 = {c["class_id"]: c for c in o2["classes"]}
    p1 = {p["key"]: p for p in o1["properties"]}
    p2 = {p["key"]: p for p in o2["properties"]}

    log(f"\n📊 Diff {v1} → {v2}")
    log("-" * 60)

    # Classes
    added = set(c2) - set(c1)
    removed = set(c1) - set(c2)
    common = set(c1) & set(c2)
    modified = [c for c in common if c1[c] != c2[c]]

    if added:
        log(f"\n➕ Classes added ({len(added)}):")
        for c in sorted(added):
            log(f"    {c} — {c2[c].get('description', '')[:60]}")
    if removed:
        log(f"\n➖ Classes removed ({len(removed)}):")
        for c in sorted(removed):
            log(f"    {c}")
    if modified:
        log(f"\n✏️ Classes modified ({len(modified)}):")
        for c in sorted(modified):
            for key in c2[c]:
                if c1[c].get(key) != c2[c].get(key):
                    log(f"    {c}.{key}: {c1[c].get(key)} → {c2[c].get(key)}")

    # Properties
    p_added = set(p2) - set(p1)
    p_removed = set(p1) - set(p2)
    p_common = set(p1) & set(p2)
    p_modified = [k for k in p_common if p1[k] != p2[k]]

    if p_added:
        log(f"\n➕ Properties added ({len(p_added)}):")
        for k in sorted(p_added):
            log(f"    {k} ({p2[k].get('datatype', '?')})")
    if p_removed:
        log(f"\n➖ Properties removed ({len(p_removed)}):")
        for k in sorted(p_removed):
            log(f"    {k}")
    if p_modified:
        log(f"\n✏️ Properties modified ({len(p_modified)}):")
        for k in sorted(p_modified):
            for key in p2[k]:
                if p1[k].get(key) != p2[k].get(key):
                    log(f"    {k}.{key}: {p1[k].get(key)} → {p2[k].get(key)}")

    if not (added or removed or modified or p_added or p_removed or p_modified):
        log("  No changes detected between versions.")


# ═══════════════════════════════════════════════════════════════════
#  SCHEMA EVOLUTION — Proposals & Changes
# ═══════════════════════════════════════════════════════════════════

def _safe_quote(name):
    """Quote a label or relationship type if it contains non-standard characters."""
    if name.isidentifier():
        return name
    return "`" + name.replace("`", "``") + "`"


def analyze_extractions(n4j, llm=None):
    """Scan the graph for high-frequency patterns that suggest schema evolution.

    Looks for:
    1. Frequently appearing entity labels not in ontology
    2. Relation patterns that don't match known property types
    3. Chunks mentioning concepts not covered by existing classes
    """
    findings = []

    with n4j.session() as s:
        # 1. Node labels not registered as OntologyClass
        labels = list(s.run("""
            CALL db.labels() YIELD label
            WHERE label <> 'OntologyClass'
              AND label <> 'Property'
              AND label <> 'Upper'
            RETURN label
            ORDER BY label
        """))
        registered = {r["class_id"] for r in s.run(
            "MATCH (c:OntologyClass) RETURN c.class_id AS class_id")}
        unregistered = [r["label"] for r in labels if r["label"] not in registered]

        if unregistered:
            for lbl in unregistered:
                qlbl = _safe_quote(lbl)
                cnt = s.run(
                    f"MATCH (n:{qlbl}) RETURN count(n) AS cnt"
                ).single()["cnt"]
                if cnt >= 3:  # threshold
                    findings.append({
                        "type": CHANGE_ADD_CLASS,
                        "target": lbl,
                        "evidence": f"Label '{lbl}' appears on {cnt} nodes, not registered in ontology",
                        "pattern_count": cnt,
                    })

        # 2. Relation types not in ontology
        reltypes = list(s.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"))
        known_rels = {"HAS_SUBJECT", "HAS_OBJECT", "RELATED_TO",
                       "HAS_CHUNK", "MENTIONS", "CONTAINS_FACT",
                       "CONTAINS_CLAIM", "WORKS_FOR", "INCLUDES",
                       "TRIGGERED", "UPDATED_CLASS", "OWNS_PROPERTY",
                       "EXTENDS", "SUPPORTS", "WEAKENS"}
        for rt in reltypes:
            rtype = rt["relationshipType"]
            if rtype in known_rels:
                continue
            qrt = _safe_quote(rtype)
            cnt = s.run(
                f"MATCH ()-[r:{qrt}]->() RETURN count(r) AS cnt"
            ).single()["cnt"]
            if cnt >= 5:
                findings.append({
                    "type": CHANGE_ADD_REL,
                    "target": rtype,
                    "evidence": f"Relationship '{rtype}' appears {cnt} times, not tracked in ontology",
                    "pattern_count": cnt,
                })

        # 3. Unclassified entities with high frequency
        high_freq = list(s.run("""
            MATCH (n)
            WHERE n.uid IS NOT NULL
              AND NOT n:OntologyClass
              AND NOT n:Property
              AND NOT n:Upper
            WITH labels(n) AS lbls, count(n) AS cnt
            WHERE cnt >= 3 AND size(lbls) = 1
            RETURN lbls[0] AS label, cnt
            ORDER BY cnt DESC
            LIMIT 20
        """))
        for hf in high_freq:
            findings.append({
                "type": CHANGE_ADD_CLASS,
                "target": hf["label"],
                "evidence": f"Label '{hf['label']}' appears {hf['cnt']} times without ontology registration",
                "pattern_count": hf["cnt"],
            })

    # Deduplicate by target
    seen = set()
    deduped = []
    for f in findings:
        key = (f["type"], f["target"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)
    return deduped


def create_proposal(n4j, changes, evidence="", source="auto-extract"):
    """Create a SchemaProposal node with proposed changes."""
    uid = make_uid("sp")
    changes_json = json.dumps(changes, default=str, indent=2)
    with n4j.session() as s:
        s.run("""
            MERGE (p:SchemaProposal {uid: $uid})
            SET p.created_at = datetime(),
                p.status = $status,
                p.proposed_changes = $changes,
                p.evidence = $evidence,
                p.source = $source,
                p.summary = $summary
        """, uid=uid, status=STATUS_REVIEW, changes=changes_json,
             evidence=evidence, source=source,
             summary=_summarize_proposal(changes))
    log(f"📋 Created SchemaProposal {uid} — {_summarize_proposal(changes)}")
    return uid


def _summarize_proposal(changes):
    """Short summary of proposed changes."""
    counts = {}
    for c in changes:
        t = c.get("type", "?")
        counts[t] = counts.get(t, 0) + 1
    parts = [f"{v}×{k}" for k, v in sorted(counts.items())]
    return ", ".join(parts) if parts else "(empty)"


def list_proposals(n4j, status_filter=None):
    """List SchemaProposal nodes."""
    where = "WHERE p.status IN $statuses" if status_filter else ""
    statuses = status_filter or [STATUS_DRAFT, STATUS_REVIEW, STATUS_APPROVED,
                                  STATUS_REJECTED, STATUS_IMPLEMENTED]
    with n4j.session() as s:
        results = list(s.run(f"""
            MATCH (p:SchemaProposal)
            {where}
            RETURN p.uid AS uid,
                   p.created_at AS created_at,
                   p.status AS status,
                   p.summary AS summary,
                   p.source AS source,
                   p.reviewed_by AS reviewed_by,
                   p.reviewed_at AS reviewed_at
            ORDER BY p.created_at DESC
            LIMIT 50
        """, statuses=statuses))
    if not results:
        log("📭 No proposals found.")
        return
    log(f"{'UID':20s} {'Created':22s} {'Status':14s} {'Source':12s} Summary")
    log("-" * 100)
    for r in results:
        dt = r["created_at"].isoformat()[:19] if r["created_at"] else "?"
        status = r["status"] or "?"
        src = r["source"] or "?"
        summary = (r["summary"] or "")[:45]
        reviewed = " ✓" if r["reviewed_by"] else ""
        log(f"{r['uid']:20s} {dt:22s} {status:14s} {src:12s} {summary}{reviewed}")


def show_proposal(n4j, uid):
    """Show full details of a proposal."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (p:SchemaProposal {uid: $uid})
            RETURN p.uid AS uid, p.created_at AS created_at, p.status AS status,
                   p.proposed_changes AS changes, p.evidence AS evidence,
                   p.source AS source, p.summary AS summary,
                   p.reviewed_by AS reviewed_by, p.reviewed_at AS reviewed_at,
                   p.review_notes AS review_notes
        """, uid=uid).single()
    if not r:
        log(f"❌ Proposal not found: {uid}")
        return
    log(f"Proposal: {r['uid']}")
    log(f"  Status:   {r['status']}")
    log(f"  Created:  {r['created_at']}")
    log(f"  Source:   {r['source']}")
    log(f"  Summary:  {r['summary']}")
    if r["reviewed_by"]:
        log(f"  Reviewed: {r['reviewed_by']} @ {r['reviewed_at']}")
    if r["review_notes"]:
        log(f"  Notes:    {r['review_notes']}")

    log(f"\n  Proposed changes:")
    changes = json.loads(r["changes"]) if isinstance(r["changes"], str) else r["changes"]
    for i, ch in enumerate(changes, 1):
        log(f"  [{i}] {ch.get('type', '?')}: {ch.get('target', '?')}")
        if ch.get("evidence"):
            log(f"       Evidence: {ch['evidence'][:80]}")
        if ch.get("before"):
            log(f"       Before:   {json.dumps(ch['before'], default=str)[:80]}")
        if ch.get("after"):
            log(f"       After:    {json.dumps(ch['after'], default=str)[:80]}")

    if r["evidence"] and r["evidence"] != "{}":
        log(f"\n  Evidence:")
        try:
            ev = json.loads(r["evidence"]) if isinstance(r["evidence"], str) else r["evidence"]
            if isinstance(ev, dict):
                for k, v in ev.items():
                    log(f"    {k}: {v}")
            else:
                log(f"    {str(ev)[:200]}")
        except (json.JSONDecodeError, TypeError):
            log(f"    {str(r['evidence'])[:200]}")


def approve_proposal(n4j, uid, reviewer="HITL", notes=""):
    """Approve a SchemaProposal and apply it as SchemaChange nodes."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (p:SchemaProposal {uid: $uid})
            WHERE p.status IN ['UNDER_REVIEW', 'DRAFT']
            SET p.status = 'APPROVED',
                p.reviewed_by = $reviewer,
                p.reviewed_at = datetime(),
                p.review_notes = $notes
            RETURN p.uid AS uid, p.proposed_changes AS changes,
                   p.source AS source, p.summary AS summary
        """, uid=uid, reviewer=reviewer, notes=notes).single()
    if not r:
        log(f"❌ Proposal {uid} not found or not in reviewable status")
        return

    changes_raw = r["changes"]
    changes = json.loads(changes_raw) if isinstance(changes_raw, str) else changes_raw
    if not changes:
        log(f"⚠️  Proposal {uid} has no changes — marked APPROVED without applying")
        return

    log(f"✅ Approved {r['uid']} — applying {len(changes)} change(s)...")
    applied = []
    for ch in changes:
        sc_uid = _apply_schema_change(n4j, ch, proposal_uid=uid, source=r["source"])
        applied.append(sc_uid)

    # Mark proposal as IMPLEMENTED
    with n4j.session() as s:
        s.run("MATCH (p:SchemaProposal {uid: $uid}) SET p.status = 'IMPLEMENTED'", uid=uid)

    # Create auto-version snapshot
    desc = f"Applied proposal {uid}: {r['summary']}"
    create_version_snapshot(n4j, description=desc, auto=True)

    log(f"✅ Applied {len(applied)} SchemaChange(s) from proposal {uid}")
    for sc in applied:
        log(f"   ├─ {sc}")
    log(f"   └─ Auto-snapshot created")


def _apply_schema_change(n4j, change, proposal_uid="", source="manual"):
    """Execute a single schema change and record it as a SchemaChange node."""
    change_type = change.get("type", "?")
    target = change.get("target", "?")
    sc_uid = make_uid("sc")
    before = change.get("before")
    after = change.get("after", {})

    if not after and change_type not in (CHANGE_REMOVE_CLASS, CHANGE_REMOVE_PROP):
        log(f"⚠️  No 'after' state for {change_type} {target} — recording without applying")
        return _record_schema_change(n4j, sc_uid, change_type, target, before, after,
                                     change.get("justification", ""), proposal_uid,
                                     source, "NOT_APPLIED")

    if change_type == CHANGE_ADD_CLASS:
        # Create a new OntologyClass node
        with n4j.session() as s:
            s.run("""
                MERGE (c:OntologyClass {class_id: $target})
                SET c.label = $label,
                    c.description = $desc,
                    c.extends = $ext,
                    c.properties = $props,
                    c.updated_at = datetime()
            """, target=target,
                 label=after.get("label", target),
                 desc=after.get("description", ""),
                 ext=after.get("extends", "Upper"),
                 props=after.get("properties", []))

    elif change_type == CHANGE_REMOVE_CLASS:
        # Soft-delete: mark as deprecated
        with n4j.session() as s:
            s.run("""
                MATCH (c:OntologyClass {class_id: $target})
                SET c.deprecated = true,
                    c.deprecated_at = datetime(),
                    c.updated_at = datetime()
            """, target=target)

    elif change_type == CHANGE_MODIFY_CLASS:
        with n4j.session() as s:
            set_clauses = ", ".join(
                f"c.{k} = ${k}" for k in after if k != "class_id"
            )
            if set_clauses:
                s.run(f"""
                    MATCH (c:OntologyClass {{class_id: $target}})
                    SET c.updated_at = datetime(),
                        {set_clauses}
                """, target=target, **after)

    elif change_type == CHANGE_ADD_PROP:
        with n4j.session() as s:
            s.run("""
                MERGE (p:Property {key: $target})
                SET p.description = $desc,
                    p.datatype = $dtype
            """, target=target,
                 desc=after.get("description", ""),
                 dtype=after.get("datatype", "string"))

    elif change_type == CHANGE_REMOVE_PROP:
        with n4j.session() as s:
            s.run("""
                MATCH (p:Property {key: $target})
                SET p.deprecated = true,
                    p.deprecated_at = datetime()
            """, target=target)

    elif change_type == CHANGE_MODIFY_PROP:
        with n4j.session() as s:
            set_clauses = ", ".join(
                f"p.{k} = ${k}" for k in after if k != "key"
            )
            if set_clauses:
                s.run(f"""
                    MATCH (p:Property {{key: $target}})
                    SET {set_clauses}
                """, target=target, **after)

    elif change_type == CHANGE_ADD_REL:
        # Relationship types are schema metadata — store as a metadata node
        with n4j.session() as s:
            s.run("""
                MERGE (rt:RelationshipType {type: $target})
                SET rt.description = $desc,
                    rt.created_at = datetime()
            """, target=target,
                 desc=after.get("description", f"Relationship type '{target}'"))

    return _record_schema_change(n4j, sc_uid, change_type, target, before, after,
                                 change.get("justification", ""), proposal_uid,
                                 source, "IMPLEMENTED")


def _record_schema_change(n4j, sc_uid, change_type, target, before, after,
                          justification, proposal_uid, source, status):
    """Create a SchemaChange node recording an evolution event."""
    with n4j.session() as s:
        s.run("""
            MERGE (sc:SchemaChange {uid: $uid})
            SET sc.timestamp = datetime(),
                sc.change_type = $change_type,
                sc.target = $target,
                sc.before = $before,
                sc.after = $after,
                sc.justification = $just,
                sc.proposal_uid = $prop,
                sc.source = $src,
                sc.status = $status

            WITH sc
            OPTIONAL MATCH (c:OntologyClass {class_id: $target})
            FOREACH (_ IN CASE WHEN c IS NOT NULL THEN [1] ELSE [] END |
                MERGE (sc)-[:UPDATED_CLASS]->(c))

            WITH sc
            OPTIONAL MATCH (p:SchemaProposal {uid: $prop})
            FOREACH (_ IN CASE WHEN p IS NOT NULL THEN [1] ELSE [] END |
                MERGE (sc)<-[:TRIGGERED]-(p))
        """, uid=sc_uid, change_type=change_type, target=target,
             before=json.dumps(before) if before else None,
             after=json.dumps(after) if after else None,
             just=justification or "",
             prop=proposal_uid, src=source, status=status)
    return sc_uid


def reject_proposal(n4j, uid, reviewer="HITL", reason=""):
    """Reject a SchemaProposal."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (p:SchemaProposal {uid: $uid})
            WHERE p.status IN ['UNDER_REVIEW', 'DRAFT']
            SET p.status = 'REJECTED',
                p.reviewed_by = $reviewer,
                p.reviewed_at = datetime(),
                p.review_notes = $notes
            RETURN p.uid AS uid, p.summary AS summary
        """, uid=uid, reviewer=reviewer, notes=reason).single()
    if r:
        log(f"❌ Rejected {r['uid']}: {r['summary']}")
    else:
        log(f"❌ Proposal {uid} not found or already reviewed")


def show_schema_history(n4j, limit=30):
    """Show recent SchemaChange events."""
    with n4j.session() as s:
        results = list(s.run("""
            MATCH (sc:SchemaChange)
            RETURN sc.uid AS uid,
                   sc.timestamp AS timestamp,
                   sc.change_type AS change_type,
                   sc.target AS target,
                   sc.status AS status,
                   sc.source AS source,
                   sc.proposal_uid AS proposal_uid
            ORDER BY sc.timestamp DESC
            LIMIT $limit
        """, limit=limit))
    if not results:
        log("📭 No schema changes recorded yet.")
        return
    log(f"📜 Schema Evolution History (last {min(len(results), limit)}):")
    log(f"{'UID':20s} {'Timestamp':22s} {'Type':18s} {'Target':20s} Status")
    log("-" * 90)
    for r in results:
        ts = r["timestamp"].isoformat()[:19] if r["timestamp"] else "?"
        log(f"{r['uid']:20s} {ts:22s} {r['change_type']:18s} {str(r['target'])[:20]:20s} {r['status'] or '?'}")


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="Phase 2: Meta-Knowledge Base (MKB)")
    sub = p.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser("init", help="Create OntologyVersion v1.0 from current ontology")
    init_p.add_argument("--description", "-d", default="Initial ontology snapshot",
                        help="Version description")

    # snapshot
    snap_p = sub.add_parser("snapshot", help="Capture current ontology as new version")
    snap_p.add_argument("--description", "-d", default="", help="Version description")

    # history
    sub.add_parser("history", help="Show ontology version history")

    # diff
    diff_p = sub.add_parser("diff", help="Diff two ontology versions")
    diff_p.add_argument("v1", help="First version (e.g. v1.0)")
    diff_p.add_argument("v2", help="Second version (e.g. v1.1)")

    # propose
    prop_p = sub.add_parser("propose", help="Create a schema evolution proposal")
    prop_p.add_argument("--file", "-f", help="JSON file with proposed changes")
    prop_p.add_argument("--source", "-s", default="manual",
                        choices=["manual", "pipeline", "auto-extract"],
                        help="Source of the proposal")
    prop_p.add_argument("--analyze", action="store_true",
                        help="Auto-analyze extraction patterns before proposing")
    prop_p.add_argument("--apply", action="store_true",
                        help="Auto-approve (skip HITL review)")

    # review / list
    sub.add_parser("review", help="List proposals pending HITL review")

    # show
    show_p = sub.add_parser("show", help="Show full proposal details")
    show_p.add_argument("uid", help="Proposal UID")

    # approve
    ap_p = sub.add_parser("approve", help="Approve a proposal and apply changes")
    ap_p.add_argument("uid", help="Proposal UID")
    ap_p.add_argument("--reviewer", "-r", default="HITL", help="Reviewer name")
    ap_p.add_argument("--notes", "-n", default="", help="Review notes")

    # reject
    rj_p = sub.add_parser("reject", help="Reject a proposal")
    rj_p.add_argument("uid", help="Proposal UID")
    rj_p.add_argument("--reviewer", "-r", default="HITL", help="Reviewer name")
    rj_p.add_argument("--reason", default="", help="Rejection reason")

    # analyze
    ana_p = sub.add_parser("analyze", help="Scan graph for schema evolution candidates")
    ana_p.add_argument("--auto-propose", action="store_true",
                       help="Auto-create proposals from findings")

    # schema-history
    hist_p = sub.add_parser("schema-history", help="Show schema change history")
    hist_p.add_argument("--limit", type=int, default=30, help="Max events to show")

    args = p.parse_args()

    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

    try:
        if args.command == "init":
            create_version_snapshot(n4j, description=args.description)

        elif args.command == "snapshot":
            create_version_snapshot(n4j, description=args.description)

        elif args.command == "history":
            show_history(n4j)

        elif args.command == "diff":
            diff_versions(n4j, args.v1, args.v2)

        elif args.command == "propose":
            changes = None
            evidence = {}

            # If --file, load from JSON
            if args.file:
                with open(args.file) as f:
                    data = json.load(f)
                changes = data.get("changes", data)
                evidence = data.get("evidence", {})

            # If --analyze, run analysis
            if args.analyze:
                findings = analyze_extractions(n4j)
                if not findings:
                    log("🔍 Analysis found no candidates for schema evolution.")
                    if changes:
                        log("   Using manually specified changes instead.")
                    else:
                        n4j.close()
                        return
                ev = {}
                for f_ in findings:
                    ev[f"pattern:{f_['target']}"] = f_["evidence"]
                evidence = ev
                changes = findings
                log(f"🔍 Analysis found {len(changes)} candidate(s) for schema evolution:")

            if not changes:
                log("❌ No changes specified. Use --file or --analyze.")
                n4j.close()
                return

            # Show preview
            for i, ch in enumerate(changes, 1):
                log(f"  [{i}] {ch.get('type', '?')}: {ch.get('target', '?')} "
                    f"({ch.get('evidence', '')[:60]})")

            # Create proposal
            uid = create_proposal(n4j, changes,
                                  evidence=json.dumps(evidence, default=str),
                                  source=args.source)

            if args.apply:
                approve_proposal(n4j, uid, reviewer="auto", notes="Auto-approved via --apply")
            else:
                log(f"📋 Proposal {uid} created — pending HITL review")

        elif args.command == "review":
            list_proposals(n4j, status_filter=[STATUS_DRAFT, STATUS_REVIEW])

        elif args.command == "show":
            show_proposal(n4j, args.uid)

        elif args.command == "approve":
            approve_proposal(n4j, args.uid, reviewer=args.reviewer, notes=args.notes)

        elif args.command == "reject":
            reject_proposal(n4j, args.uid, reviewer=args.reviewer, reason=args.reason)

        elif args.command == "analyze":
            findings = analyze_extractions(n4j)
            if not findings:
                log("🔍 No schema evolution candidates found.")
            else:
                log(f"\n🔍 Found {len(findings)} schema evolution candidate(s):")
                for f_ in findings:
                    log(f"\n  [{f_['type']}] {f_['target']}")
                    log(f"       Evidence: {f_['evidence']}")
                    log(f"       Count:    {f_['pattern_count']}")
                if args.auto_propose:
                    uid = create_proposal(n4j, findings,
                                          evidence=json.dumps(
                                              {f_["target"]: f_["evidence"]
                                               for f_ in findings}),
                                          source="auto-extract")
                    log(f"📋 Auto-created proposal {uid} from analysis")

        elif args.command == "schema-history":
            show_schema_history(n4j, args.limit)

    finally:
        n4j.close()


if __name__ == "__main__":
    main()