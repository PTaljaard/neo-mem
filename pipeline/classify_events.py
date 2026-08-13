#!/usr/bin/env python3
"""
classify_events.py — Batch topic classification for Event nodes.

Option B: Auto-classify all unclassified events using DeepSeek.
Option C: HITL review of proposed/new topics.

Usage:
    python3 classify_events.py classify              # Batch classify all unclassified events
    python3 classify_events.py classify --dry-run    # Count without writing
    python3 classify_events.py review                # List topics pending HITL review
    python3 classify_events.py review --proposals    # List proposed (auto-extract) topics
    python3 classify_events.py approve <topic>       # Approve a proposed topic
    python3 classify_events.py reject <topic>        # Reject a proposed topic
    python3 classify_events.py topics                # List all topics with event counts
"""
import sys, json, argparse, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE, CHAT_MODEL
from neo4j import GraphDatabase
from openai import OpenAI

log = print

# ── Seed Topics ──────────────────────────────────────────────────────
SEED_TOPICS = {
    "Health": {"description": "Personal health, diet, exercise, sleep, supplements", "parent": None, "color": "#27ae60"},
    "  Keto": {"description": "Ketogenic diet, macros, ketosis", "parent": "Health", "color": "#2ecc71"},
    "  Carnivore": {"description": "Carnivore diet, animal-based nutrition", "parent": "Health", "color": "#2ecc71"},
    "  Dr Boz": {"description": "Dr Boz (Annette Bosworth) — keto, metabolic health", "parent": "Health", "color": "#2ecc71"},
    "  Dr Berg": {"description": "Dr Eric Berg — keto, nutrition, fasting", "parent": "Health", "color": "#2ecc71"},
    "  Huberman": {"description": "Andrew Huberman — neuroscience, protocols, exercise", "parent": "Health", "color": "#2ecc71"},
    "  Exercise": {"description": "Exercise for sedentary lifestyle, mobility, strength", "parent": "Health", "color": "#2ecc71"},
    
    "Strategy": {"description": "Strategic thinking, decision-making, frameworks", "parent": None, "color": "#8e44ad"},
    "  Cynefin": {"description": "Cynefin framework — sense-making, complexity", "parent": "Strategy", "color": "#9b59b6"},
    "  Complexity": {"description": "Complex systems, emergence, chaos theory", "parent": "Strategy", "color": "#9b59b6"},
    
    "AI & Agents": {"description": "AI, LLMs, autonomous agents, tool-use", "parent": None, "color": "#2980b9"},
    "  Hermes": {"description": "Hermes Agent — configuration, workflows, tools", "parent": "AI & Agents", "color": "#3498db"},
    "  RAG": {"description": "Retrieval-Augmented Generation, vector search, GraphRAG", "parent": "AI & Agents", "color": "#3498db"},
    
    "Geopolitics": {"description": "International relations, conflicts, diplomacy", "parent": None, "color": "#c0392b"},
    "  SA Politics": {"description": "South African politics, governance, economy", "parent": "Geopolitics", "color": "#e74c3c"},
    "  US-Iran": {"description": "US-Iran relations, nuclear deal, Middle East", "parent": "Geopolitics", "color": "#e74c3c"},
    "  Russia-Ukraine": {"description": "Russia-Ukraine war, NATO, European security", "parent": "Geopolitics", "color": "#e74c3c"},
    "  ICJ": {"description": "International Court of Justice, SA genocide case", "parent": "Geopolitics", "color": "#e74c3c"},
    
    "SA Commentators": {"description": "South African economic/political commentators", "parent": None, "color": "#d35400"},
    "  Dawie Roodt": {"description": "Dawie Roodt — economist, commentator", "parent": "SA Commentators", "color": "#e67e22"},
    "  Frans Cronje": {"description": "Frans Cronje — political analyst, author", "parent": "SA Commentators", "color": "#e67e22"},
    "  Azar Jammine": {"description": "Azar Jammine — chief economist, Econometrix", "parent": "SA Commentators", "color": "#e67e22"},
    "  Rob Hersov": {"description": "Rob Hersov — economist, activist", "parent": "SA Commentators", "color": "#e67e22"},
    
    "Genealogy": {"description": "Family history, ancestry research, lineage", "parent": None, "color": "#16a085"},
    "  Taillard": {"description": "Taillard family — Tournai, France, Huguenot ancestry", "parent": "Genealogy", "color": "#1abc9c"},
    
    "CPS Research": {"description": "Cyber-Physical-Social research for PhD", "parent": None, "color": "#2c3e50"},
    "  KG Construction": {"description": "Knowledge graph construction, ontology, schemas", "parent": "CPS Research", "color": "#34495e"},
    "  Event Extraction": {"description": "Entity-event and event-event extraction (EV+VV)", "parent": "CPS Research", "color": "#34495e"},
    "  Governance": {"description": "HITL governance, fact validation, schema evolution", "parent": "CPS Research", "color": "#34495e"},
    
    "Personal": {"description": "Personal finance, home, travel, daily life", "parent": None, "color": "#7f8c8d"},
}

# ── LLM Config ───────────────────────────────────────────────────────
def get_or_key():
    """Get OpenRouter API key from various sources."""
    import os
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    # Try ~/.hermes/.env (Hermes Desktop stores keys here)
    try:
        env_path = Path.home() / ".hermes" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "OPENROUTER_API_KEY" in line:
                    key = line.split("=", 1)[1].strip().strip("'\"")
                    if key:
                        return key
    except:
        pass
    # Try subprocess
    try:
        import subprocess
        r = subprocess.run(["powershell", "-Command", "[Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY')"],
                          capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            return r.stdout.strip()
    except:
        pass
    return ""


def get_llm(model="gemma4", api_key_override=""):
    """Get LLM client for the specified model."""
    if model == "gemma4":
        return OpenAI(base_url=OLLAMA_BASE, api_key="ollama")
    elif model == "deepseek":
        key = api_key_override or get_or_key()
        if not key:
            log("⚠️  No OpenRouter API key found — falling back to gemma4")
            return get_llm("gemma4")
        return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)
    return get_llm("gemma4")


MODEL_MAP = {
    "gemma4": CHAT_MODEL,
    "deepseek": "deepseek/deepseek-v4-flash-0731",
}


def classify_batch(llm, events_batch, existing_topics, model="gemma4"):
    """Classify a batch of events into topics. Returns list of {event_uid, topics[]}."""
    model_name = MODEL_MAP.get(model, CHAT_MODEL)
    
    topics_list = "\n".join(f"  - {t}" for t in sorted(existing_topics))
    events_text = "\n".join(f"[{i}] {e}" for i, e in enumerate(events_batch))
    
    prompt = f"""You are classifying events into topics. Each event can belong to MULTIPLE topics.

Available topics:
{topics_list}

Rules:
1. Assign 1-3 topics per event
2. An event can belong to multiple topics if it spans them
3. If an event doesn't fit any existing topic, suggest a NEW topic (prefixed with "NEW: ")
4. Only suggest a new topic if genuinely no existing topic fits

Events to classify:
{events_text}

Return ONLY a JSON array. No explanations, no markdown.
[
  {{"index": 0, "topics": ["Geopolitics", "US-Iran"]}},
  {{"index": 1, "topics": ["Health", "Huberman"]}}
]"""

    try:
        resp = llm.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=2048
        )
        raw = resp.choices[0].message.content or "[]"
        import re
        raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
        raw = re.sub(r'\s*```', '', raw)
        s, e = raw.find('['), raw.rfind(']')
        if s >= 0 and e > s:
            return json.loads(raw[s:e+1])
        return []
    except Exception as e:
        log(f"  ⚠️ Classification failed: {e}")
        return []


def seed_topics(n4j):
    """Ensure seed topics exist in Neo4j. Creates if missing."""
    created = 0
    with n4j.session() as s:
        for name, info in SEED_TOPICS.items():
            name_clean = name.strip()
            r = s.run("MATCH (t:Topic {name: $n}) RETURN t.name", n=name_clean).single()
            if not r:
                s.run("""
                    CREATE (t:Topic {name: $n, description: $d, color: $c, 
                           parent: $p, source: 'seed', created_at: datetime(),
                           status: 'active'})
                """, n=name_clean, d=info["description"], c=info["color"],
                     p=info["parent"])
                created += 1
    if created:
        log(f"🌱 Created {created} new seed topic(s)")


def classify_all(n4j, llm, model="gemma4", dry_run=False, batch_size=10):
    """Classify all events that don't have BELONGS_TO edges yet."""
    with n4j.session() as s:
        # Get unclassified events
        r = s.run("""
            MATCH (e:Event)
            WHERE NOT (e)-[:BELONGS_TO]->(:Topic)
            RETURN e.uid AS uid, e.description AS description, e.name AS name
            ORDER BY e.uid
        """)
        events = [(row["uid"], row["description"] or row["name"]) for row in r]
    
    if not events:
        log("📭 All events already classified. Nothing to do.")
        return
    
    # Get existing topic names
    with n4j.session() as s:
        r = s.run("MATCH (t:Topic) RETURN t.name ORDER BY t.name")
        existing_topics = {row["t.name"] for row in r}
    
    log(f"📊 {len(events)} unclassified events, {len(existing_topics)} existing topics")
    
    if dry_run:
        log(f"   (dry-run — would process {len(events)} events in {len(events)//batch_size + 1} batches)")
        return
    
    total_classified = 0
    new_topics_proposed = set()
    
    for i in range(0, len(events), batch_size):
        batch = events[i:i+batch_size]
        batch_texts = [e[1] for e in batch]
        batch_uids = [e[0] for e in batch]
        
        log(f"  Batch {i//batch_size + 1}/{(len(events)-1)//batch_size + 1}: {len(batch)} events")
        
        results = classify_batch(llm, batch_texts, existing_topics | new_topics_proposed, model)
        
        if not results:
            log("    ⚠️ LLM returned empty — retrying...")
            time.sleep(2)
            results = classify_batch(llm, batch_texts, existing_topics | new_topics_proposed, model)
            if not results:
                log("    ❌ Retry failed, skipping batch")
                continue
        
        with n4j.session() as s:
            for result in results:
                idx = result.get("index")
                topics = result.get("topics", [])
                if idx is None or idx >= len(batch):
                    continue
                uid = batch_uids[idx]
                
                for topic_name in topics:
                    topic_clean = topic_name.strip()
                    if topic_clean.startswith("NEW: "):
                        topic_clean = topic_clean[5:]
                        new_topics_proposed.add(topic_clean)
                        # Create with proposed status
                        s.run("""
                            MERGE (t:Topic {name: $n})
                            ON CREATE SET t.source = 'auto-extract', 
                                          t.status = 'proposed',
                                          t.created_at = datetime(),
                                          t.description = 'Auto-proposed topic'
                            ON MATCH SET t.status = CASE WHEN t.status IS NULL THEN 'proposed' ELSE t.status END
                        """, n=topic_clean)
                    
                    # Create BELONGS_TO edge
                    s.run("""
                        MATCH (e:Event {uid: $uid})
                        MATCH (t:Topic {name: $topic})
                        MERGE (e)-[:BELONGS_TO]->(t)
                    """, uid=uid, topic=topic_clean)
                    total_classified += 1
        
        time.sleep(1)  # Rate limiting
    
    log(f"\n✅ Classified {total_classified} event-topic edges across {len(events)} events")
    if new_topics_proposed:
        log(f"💡 {len(new_topics_proposed)} new topics proposed for HITL review:")
        for nt in sorted(new_topics_proposed):
            log(f"     - {nt}")
        log(f"   Run `classify_events.py review --proposals` to review")


def list_topics(n4j):
    """List all topics with event counts."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (t:Topic)
            OPTIONAL MATCH (t)<-[:BELONGS_TO]-(e:Event)
            RETURN t.name AS topic, t.description AS desc, t.status AS status,
                   t.source AS source, t.parent AS parent,
                   count(e) AS events
            ORDER BY events DESC, t.name
        """)
        results = list(r)
    
    if not results:
        log("📭 No topics found. Run `seed` or `classify` first.")
        return
    
    log(f"{'Topic':30s} {'Events':8s} {'Status':12s} {'Source':12s} Description")
    log("-" * 90)
    for row in results:
        events = row["events"]
        status = row["status"] or "active"
        src = row["source"] or "?"
        desc = (row["desc"] or "")[:40]
        log(f"{row['topic']:30s} {events:<8d} {status:12s} {src:12s} {desc}")
    
    log(f"\n📊 {len(results)} topics total")


def list_proposals(n4j):
    """List proposed topics pending HITL review."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (t:Topic)
            WHERE t.status = 'proposed' OR t.source = 'auto-extract'
            OPTIONAL MATCH (t)<-[:BELONGS_TO]-(e:Event)
            RETURN t.name AS topic, t.description AS desc, t.source AS source,
                   count(e) AS events
            ORDER BY events DESC
        """)
        results = list(r)
    
    if not results:
        log("📭 No proposed topics pending review.")
        return
    
    log(f"\n💡 **{len(results)} topics proposed for HITL review**")
    log(f"{'Topic':30s} {'Events':8s} {'Source':12s} {'Description':40s}")
    log("-" * 90)
    for row in results:
        desc = (row["desc"] or "")[:40]
        log(f"{row['topic']:30s} {row['events']:<8d} {row['source']:12s} {desc}")
    log(f"\nRun: `classify_events.py approve <topic>` or `reject <topic>`")


def approve_topic(n4j, topic_name):
    """Approve a proposed topic."""
    with n4j.session() as s:
        r = s.run("""
            MATCH (t:Topic {name: $n})
            SET t.status = 'active', t.approved_at = datetime()
            RETURN t.name, t.description
        """, n=topic_name).single()
    if r:
        log(f"✅ Approved topic: {topic_name}")
    else:
        log(f"❌ Topic not found: {topic_name}")


def reject_topic(n4j, topic_name):
    """Reject a proposed topic — remove it and its BELONGS_TO edges."""
    with n4j.session() as s:
        # Count events before deletion
        cnt = s.run("""
            MATCH (e:Event)-[:BELONGS_TO]->(t:Topic {name: $n})
            RETURN count(e) AS events
        """, n=topic_name).single()["events"]
        
        s.run("""
            MATCH (t:Topic {name: $n})
            DETACH DELETE t
        """, n=topic_name)
    log(f"🗑️ Rejected topic: {topic_name} (removed from {cnt} events)")


def main():
    p = argparse.ArgumentParser(description="Topic classification for Event nodes")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--model", choices=["gemma4", "deepseek"], default="deepseek")
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--api-key", help="OpenRouter API key (or set OPENROUTER_API_KEY)")
    sub = p.add_subparsers(dest="command", required=True)
    
    sub.add_parser("seed", help="Create seed topics in Neo4j")
    
    clf = sub.add_parser("classify", help="Classify unclassified events")
    clf.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    clf.add_argument("--model", choices=["gemma4", "deepseek"], default="deepseek", help=argparse.SUPPRESS)
    clf.add_argument("--batch-size", type=int, default=10, help=argparse.SUPPRESS)
    clf.add_argument("--api-key", help=argparse.SUPPRESS)
    
    sub.add_parser("topics", help="List all topics with event counts")
    sub.add_parser("review", help="List topics pending HITL review")
    sub.add_parser("proposals", help="List proposed topics")
    
    apv = sub.add_parser("approve", help="Approve a proposed topic")
    apv.add_argument("topic", help="Topic name to approve")
    
    rej = sub.add_parser("reject", help="Reject a proposed topic")
    rej.add_argument("topic", help="Topic name to reject")
    
    args = p.parse_args()
    
    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    
    if args.command == "seed":
        seed_topics(n4j)
    
    elif args.command == "classify":
        seed_topics(n4j)  # Ensure seeds exist first
        llm = get_llm(args.model, args.api_key)
        classify_all(n4j, llm, args.model, args.dry_run, args.batch_size)
    
    elif args.command == "topics":
        list_topics(n4j)
    
    elif args.command == "review" or args.command == "proposals":
        list_proposals(n4j)
    
    elif args.command == "approve":
        approve_topic(n4j, args.topic)
    
    elif args.command == "reject":
        reject_topic(n4j, args.topic)
    
    n4j.close()


if __name__ == "__main__":
    main()