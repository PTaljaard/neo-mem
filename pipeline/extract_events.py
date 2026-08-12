#!/usr/bin/env python3
"""
# extract_events.py — Entity-Event & Event-Event Extraction
#
# Inspired by AutoSchemaKG (HKUST-KnowComp, ACL 2026):
#   Bai et al., "AutoSchemaKG: Autonomous Knowledge Graph Construction
#   through Dynamic Schema Induction from Web-Scale Corpora"
#   https://arxiv.org/abs/2505.23628 | https://github.com/HKUST-KnowComp/AutoSchemaKG
#   MIT License
#
# Their approach (triple_extraction_prompt.py):
#   Three extraction types: Entity-Entity (EE), Entity-Event (EV),
#   Event-Event (VV). We already have EE. This adds EV + VV.
#
# EV: Given a passage, identify events (as sentences) and entities
#     that participate in each event. Creates (:Event)-[:PARTICIPATES_IN]-(:Entity)
#
# VV: Given events found in a passage, identify temporal and causal
#     relationships: BEFORE, AFTER, SIMULTANEOUS, CAUSES, RESULTS_IN
#     Creates (:Event)-[:BEFORE]->(:Event) etc.
#
# Usage:
#   python3 extract_events.py                          # Process all unprocessed chunks
#   python3 extract_events.py --limit 50                # Process first 50 chunks
#   python3 extract_events.py --doc autoschemakg-2505.23628  # Process one doc
#   python3 extract_events.py --dry-run                 # Count without writing
#
# No re-ingestion needed — reads existing chunks from Neo4j.
"""

import sys, json, datetime, time, re, hashlib, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE, CHAT_MODEL
from neo4j import GraphDatabase
from openai import OpenAI

log = print

# ── Entity-Event (EV) prompt ──────────────────────────────────────────
# Ref: AutoSchemaKG triple_extraction_prompt.py — event_entity section
EV_PROMPT = """Analyze the following passage and extract events with their participating entities.

An EVENT is a single sentence describing something that happens. Each event has ENTITIES that participate in it.

Return ONLY valid JSON. No markdown, no code fences.
{{"events": [
  {{"event": "sentence describing the event",
   "entities": ["entity1", "entity2"]}}
]}}

Passage:
{text}"""

# ── Event-Event (VV) prompt ──────────────────────────────────────────
# Ref: AutoSchemaKG triple_extraction_prompt.py — event_relation section
VV_PROMPT = """Analyze these events and identify temporal and causal relationships between them.

Relationship types:
- BEFORE: event1 happens before event2
- AFTER: event1 happens after event2  
- SIMULTANEOUS: events happen at the same time
- CAUSES: event1 causes event2
- RESULTS_IN: event1 results in event2

Return ONLY valid JSON. No markdown, no code fences.
{{"relations": [
  {{"head": "event text", "relation": "BEFORE|AFTER|SIMULTANEOUS|CAUSES|RESULTS_IN", "tail": "event text"}}
]}}

Events:
{events}

Passage context:
{text}"""


def repair_json(raw):
    """Tolerant JSON parser for LLM output."""
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
    raw = re.sub(r'\s*```', '', raw)
    s, e = raw.find('{'), raw.rfind('}')
    if s == -1 or e == -1:
        return {}
    raw = raw[s:e+1]
    try:
        return json.loads(raw)
    except:
        raw = re.sub(r',\s*}', '}', raw)
        raw = re.sub(r',\s*]', ']', raw)
        try:
            return json.loads(raw)
        except:
            return {}


def event_uid(event_text, chunk_id, idx):
    """Deterministic UID for an event."""
    h = hashlib.md5(event_text.encode()).hexdigest()[:8]
    return f"evt-{chunk_id}-{idx}-{h}"


def extract_events_from_chunk(llm, chunk_text, chunk_id):
    """Run EV extraction on one chunk. Returns list of event dicts."""
    prompt = EV_PROMPT.format(text=chunk_text[:8000])
    try:
        resp = llm.chat.completions.create(
            model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=1024
        )
        data = repair_json(resp.choices[0].message.content)
        events = data.get("events", [])
        # Validate
        valid = []
        for ev in events:
            if isinstance(ev, dict) and "event" in ev and ev["event"].strip():
                valid.append({
                    "event": ev["event"].strip()[:200],
                    "entities": [e.strip() for e in ev.get("entities", []) if e.strip()][:10]
                })
        return valid
    except Exception as e:
        log(f"  ⚠️ EV extraction failed: {e}")
        return []


def extract_relations(llm, events, chunk_text, chunk_id):
    """Run VV extraction on a list of events. Returns list of relation dicts."""
    if len(events) < 2:
        return []

    events_text = "\n".join(f"{i+1}. {e['event']}" for i, e in enumerate(events))
    prompt = VV_PROMPT.format(events=events_text, text=chunk_text[:4000])
    try:
        resp = llm.chat.completions.create(
            model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=1024
        )
        data = repair_json(resp.choices[0].message.content)
        relations = data.get("relations", [])
        # Validate
        valid = []
        valid_rels = {"BEFORE", "AFTER", "SIMULTANEOUS", "CAUSES", "RESULTS_IN"}
        valid_events = {e["event"] for e in events}
        for rel in relations:
            if isinstance(rel, dict) and rel.get("relation") in valid_rels:
                valid.append(rel)
        return valid
    except Exception as e:
        log(f"  ⚠️ VV extraction failed: {e}")
        return []


def store_in_neo4j(n4j, chunk_id, events, relations):
    """Store Event nodes, PARTICIPATES_IN edges, and VV edges."""
    event_uids = {}
    with n4j.session() as s:
        for i, ev in enumerate(events):
            uid = event_uid(ev["event"], chunk_id, i)
            event_uids[ev["event"]] = uid

            # Create Event node
            s.run("""
                MERGE (e:Event {uid: $u})
                SET e.name = $n, e.description = $d,
                    e.source_chunk = $c, e.extracted_at = datetime(),
                    e.governance_status = 'PENDING'
            """, u=uid, n=ev["event"][:60], d=ev["event"], c=chunk_id)

            # Link Event → Chunk
            s.run("""
                MATCH (ev:Event {uid: $eu}), (ch:Chunk {uid: $cu})
                MERGE (ev)-[:MENTIONS]->(ch)
            """, eu=uid, cu=chunk_id)

            # Link Event → Entity via PARTICIPATES_IN
            for ent_name in ev["entities"]:
                s.run("""
                    OPTIONAL MATCH (en:Entity {name: $en})
                    WITH en
                    MATCH (ev:Event {uid: $eu})
                    WHERE en IS NOT NULL
                    MERGE (ev)-[:PARTICIPATES_IN]->(en)
                """, en=ent_name, eu=uid)

        # Store VV relations
        for rel in relations:
            head_uid = event_uids.get(rel["head"])
            tail_uid = event_uids.get(rel["tail"])
            if head_uid and tail_uid:
                rel_type = rel["relation"]
                s.run(f"""
                    MATCH (h:Event {{uid: $hu}}), (t:Event {{uid: $tu}})
                    MERGE (h)-[:{rel_type}]->(t)
                """, hu=head_uid, tu=tail_uid)

    return len(events), len(relations)


def main():
    p = argparse.ArgumentParser(description="Extract events from existing chunks (AutoSchemaKG-inspired)")
    p.add_argument("--limit", type=int, default=50, help="Max chunks to process")
    p.add_argument("--doc", help="Process only chunks from a specific doc UID")
    p.add_argument("--dry-run", action="store_true", help="Count without writing")
    p.add_argument("--batch", type=int, default=5, help="LLM batch size")
    args = p.parse_args()

    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")

    # Fetch unprocessed chunks (no Event linked to them)
    doc_filter = "AND c.doc_id = $d" if args.doc else ""
    with n4j.session() as s:
        r = s.run(f"""
            MATCH (c:Chunk)
            WHERE NOT (c)<-[:MENTIONS]-(:Event) {doc_filter}
            AND c.uid IS NOT NULL AND size(c.text) > 100
            RETURN c.uid AS source_id, c.text AS source_text, c.doc_id AS doc, 'chunk' AS source_type
            LIMIT $limit
        """, d=args.doc, limit=args.limit)
        items = [(row["source_id"], row["source_text"], row["doc"], row["source_type"]) for row in r]

        # Also process NewsArticle body text (only if --doc not specified)
        items2 = []
        if not args.doc:
            r2 = s.run("""
                MATCH (n:NewsArticle)
                WHERE NOT (n)<-[:MENTIONS]-(:Event)
                AND n.uid IS NOT NULL AND size(n.body) > 100
                RETURN n.uid AS source_id, n.body AS source_text, n.title AS doc, 'news' AS source_type
                ORDER BY n.fetched_at DESC
                LIMIT $limit
            """, limit=args.limit)
            items2 = [(row["source_id"], row["source_text"], row["doc"], row["source_type"]) for row in r2]
        # Merge, preferring news first
        items = items2 + items

    log(f"🔍 Found {len(items)} sources to process for events ({len(items2)} news, {len(items) - len(items2)} chunks)")
    # Also update the store function to handle NewsArticle linking
    if args.dry_run:
        for uid, text, doc_id, stype in items[:5]:
            log(f"  📝 {uid} ({stype}/{doc_id}): {len(text)} chars")
        n4j.close()
        return

    total_events = 0
    total_relations = 0
    source_count = 0

    for source_id, source_text, doc, stype in items:
        if not source_text or len(source_text.strip()) < 100:
            continue

        events = extract_events_from_chunk(llm, source_text, source_id)
        if not events:
            continue

        relations = extract_relations(llm, events, source_text, source_id)

        # Store
        with n4j.session() as s:
            for i, ev in enumerate(events):
                uid = event_uid(ev["event"], source_id, i)
                s.run("""
                    MERGE (e:Event {uid: $u})
                    SET e.name = $n, e.description = $d,
                        e.source_id = $si, e.source_type = $st,
                        e.extracted_at = datetime(), e.governance_status = 'PENDING'
                """, u=uid, n=ev["event"][:60], d=ev["event"], si=source_id, st=stype)

                # Link to source (Chunk or NewsArticle)
                if stype == "news":
                    s.run("""
                        MATCH (ev:Event {uid: $eu}), (src:NewsArticle {uid: $su})
                        MERGE (ev)-[:MENTIONS]->(src)
                    """, eu=uid, su=source_id)
                else:
                    s.run("""
                        MATCH (ev:Event {uid: $eu}), (src:Chunk {uid: $su})
                        MERGE (ev)-[:MENTIONS]->(src)
                    """, eu=uid, su=source_id)

                # Link to entities
                for ent_name in ev["entities"]:
                    s.run("""
                        OPTIONAL MATCH (en:Entity {name: $en})
                        WITH en
                        MATCH (ev:Event {uid: $eu})
                        WHERE en IS NOT NULL
                        MERGE (ev)-[:PARTICIPATES_IN]->(en)
                    """, en=ent_name, eu=uid)

            # Store VV relations
            event_uids = {ev["event"]: event_uid(ev["event"], source_id, i) for i, ev in enumerate(events)}
            for rel in relations:
                head_uid = event_uids.get(rel["head"])
                tail_uid = event_uids.get(rel["tail"])
                if head_uid and tail_uid:
                    rel_type = rel["relation"]
                    s.run(f"""
                        MATCH (h:Event {{uid: $hu}}), (t:Event {{uid: $tu}})
                        MERGE (h)-[:{rel_type}]->(t)
                    """, hu=head_uid, tu=tail_uid)

        total_events += len(events)
        total_relations += len(relations)
        source_count += 1
        log(f"  ✅ {source_id[:30]:30s} {len(events)} events, {len(relations)} relations")
        sys.stdout.flush()

        if source_count % args.batch == 0:
            time.sleep(1)

    # Summary
    with n4j.session() as s:
        r = s.run("MATCH (e:Event) RETURN count(e) AS total").single()
        r2 = s.run("""
            MATCH (e:Event)-[r]->(e2:Event) 
            RETURN type(r) AS rel, count(r) AS cnt 
            ORDER BY cnt DESC
        """).data()
    log(f"\n📊 Summary: {source_count} sources → {total_events} events, {total_relations} VV relations")
    log(f"   Total Event nodes: {r['total']}")
    for row in r2:
        log(f"   {row['rel']:15s} {row['cnt']}")

    n4j.close()


if __name__ == "__main__":
    main()