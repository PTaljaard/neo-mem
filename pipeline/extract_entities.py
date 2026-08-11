#!/usr/bin/env python3
"""Rabust entity/fact extraction from Neo4j chunks using LLM with retry & JSON repair."""
import os, sys, uuid, logging, json, re, time
from neo4j import GraphDatabase
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("extract-robust")

# ── Config ────────────────────────────────────────────────────────────
NEO4J_URI = "bolt://192.168.0.114:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "Erna#26neo4j"
DOC_UID = "dial-kg-2603.20059"
OLLAMA_BASE = "http://192.168.0.200:11434/v1"
EXTRACT_MODEL = "gemma4:e4b-it-qat"
MAX_RETRIES = 3

# ── Helpers ───────────────────────────────────────────────────────────

def repair_json(raw: str) -> dict:
    """Attempt to repair and parse JSON from LLM output."""
    # Strip markdown code fences
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
    # Find first { and last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1:
        return {"entities": [], "facts": []}
    raw = raw[start:end+1]
    # Try standard parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Fix common issues: trailing commas, single quotes, unquoted keys
    raw = re.sub(r',\s*}', '}', raw)  # trailing commas
    raw = re.sub(r',\s*]', ']', raw)  # trailing commas in arrays
    raw = re.sub(r"(?<!\\)'", '"', raw)  # single → double quotes
    raw = re.sub(r'(\w+):', r'"\1":', raw)  # unquoted keys
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"entities": [], "facts": []}

def extract_entities_and_facts(llm, text, retries=MAX_RETRIES):
    """Extract with retry on failure."""
    prompt = f"""Extract entities and facts from this text about the DIAL-KG paper.

Return ONLY valid JSON with this exact structure:
{{"entities": [{{"name": "...", "type": "PAPER|METHOD|CONCEPT|DATASET|PERSON|METRIC|TASK|SYSTEM"}}],
 "facts": [{{"subject": "...", "relation": "relation_verb", "object": "..."}}]}}

Rules:
- entities: unique named things. Use full canonical names.
- facts: subject-relation-object triples explicitly stated in the text.
- relation: lowercase with underscores (e.g. proposes, achieves, improves_upon).
- Max 15 entities, 10 facts per chunk.
- If none found, return {{"entities": [], "facts": []}}

Text:
{text[:1500]}"""

    for attempt in range(retries):
        try:
            resp = llm.chat.completions.create(
                model=EXTRACT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=1024
            )
            content = resp.choices[0].message.content.strip()
            data = repair_json(content)
            if "entities" in data and "facts" in data:
                # Deduplicate by name
                seen = set()
                unique = []
                for e in data["entities"]:
                    if isinstance(e, dict) and "name" in e and e["name"] not in seen:
                        seen.add(e["name"])
                        unique.append(e)
                # Filter facts where subject/object exist in entities
                valid_facts = []
                for f in data["facts"]:
                    if isinstance(f, dict) and "subject" in f and "object" in f and "relation" in f:
                        if f["subject"] in seen or f["object"] in seen:
                            valid_facts.append(f)
                return unique, valid_facts
        except Exception as ex:
            log.warning(f"Attempt {attempt+1} failed: {ex}")
            time.sleep(1)
    return [], []

def store_in_neo4j(n4j, chunk_uid, entities, facts):
    """Atomic batch store."""
    with n4j.session() as s:
        tx = s.begin_transaction()
        try:
            for ent in entities:
                eid = f"ent-{uuid.uuid4().hex[:8]}"
                tx.run("MERGE (e:Entity {name: $n}) SET e.type = $t, e.uid = COALESCE(e.uid, $u)",
                       n=ent["name"], t=ent["type"], u=eid)
                tx.run("MATCH (c:Chunk {uid: $cu}), (e:Entity {name: $en}) MERGE (c)-[:MENTIONS]->(e)",
                       cu=chunk_uid, en=ent["name"])
            for fact in facts:
                fid = f"fact-{uuid.uuid4().hex[:8]}"
                ft = f"{fact['subject']} {fact['relation']} {fact['object']}"
                tx.run("MERGE (f:Fact {uid: $u}) SET f.text = $t", u=fid, t=ft)
                tx.run("MATCH (c:Chunk {uid: $cu}), (f:Fact {uid: $fu}) MERGE (c)-[:CONTAINS_FACT]->(f)",
                       cu=chunk_uid, fu=fid)
                tx.run("MATCH (f:Fact {uid: $fu}), (s:Entity {name: $sn}) MERGE (f)-[:HAS_SUBJECT]->(s)",
                       fu=fid, sn=fact["subject"])
                tx.run("MATCH (f:Fact {uid: $fu}), (o:Entity {name: $on}) MERGE (f)-[:HAS_OBJECT]->(o)",
                       fu=fid, on=fact["object"])
                tx.run("MATCH (s:Entity {name: $sn}), (o:Entity {name: $on}) MERGE (s)-[:RELATED_TO]-(o)",
                       sn=fact["subject"], on=fact["object"])
            tx.commit()
        except Exception:
            tx.rollback()
            raise

def main():
    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")

    # Get all chunks
    with n4j.session() as s:
        result = s.run(
            "MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c:Chunk) "
            "RETURN c.uid, c.text, c.chunk_index ORDER BY c.chunk_index",
            u=DOC_UID
        )
        chunks = [{"uid": r["c.uid"], "text": r["c.text"], "idx": r["c.chunk_index"]} for r in result]
    log.info(f"Found {len(chunks)} chunks")

    # Check which chunks already have entities
    with n4j.session() as s:
        result = s.run(
            "MATCH (c:Chunk)-[:MENTIONS]->(e) WHERE c.doc_id = $d "
            "RETURN c.uid AS uid, COUNT(e) AS n", d=DOC_UID
        )
        done = {r["uid"] for r in result}
    log.info(f"{len(done)} chunks already processed")

    total_e, total_f = 0, 0
    for i, chunk in enumerate(chunks):
        if chunk["uid"] in done:
            log.info(f"  Chunk {i+1}/{len(chunks)} — SKIP (already done)")
            continue
        log.info(f"  Chunk {i+1}/{len(chunks)} ({chunk['uid']})")
        entities, facts = extract_entities_and_facts(llm, chunk["text"])
        if entities or facts:
            try:
                store_in_neo4j(n4j, chunk["uid"], entities, facts)
                total_e += len(entities)
                total_f += len(facts)
                log.info(f"    → {len(entities)} entities, {len(facts)} facts")
            except Exception as ex:
                log.error(f"    → STORE FAILED: {ex}")

    log.info(f"\nDONE: extracted {total_e} entities, {total_f} facts")

    # Verify
    with n4j.session() as s:
        r = s.run("MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c)-[:MENTIONS]->(e) "
                  "RETURN COUNT(DISTINCT e) AS e, COUNT(DISTINCT c) AS c", u=DOC_UID)
        for row in r:
            log.info(f"Graph: {row['e']} entities linked to {row['c']} chunks")
        r = s.run("MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c)-[:CONTAINS_FACT]->(f) "
                  "RETURN COUNT(DISTINCT f) AS f", u=DOC_UID)
        for row in r:
            log.info(f"Graph: {row['f']} facts")

    n4j.close()

if __name__ == "__main__":
    main()