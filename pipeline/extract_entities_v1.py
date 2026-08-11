#!/usr/bin/env python3
"""Extract entities and facts from DIAL-KG chunks in Neo4j using LLM."""
import os, sys, uuid, logging, json, re
from neo4j import GraphDatabase
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("extract-entities")

NEO4J_URI = "bolt://192.168.0.114:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "Erna#26neo4j"
DOC_UID = "dial-kg-2603.20059"

# LLM for extraction
OLLAMA_BASE = "http://192.168.0.200:11434/v1"
EXTRACT_MODEL = "gemma4:e4b-it-qat"

def get_chunks(n4j):
    """Get all chunks for the document."""
    with n4j.session() as s:
        result = s.run(
            "MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c:Chunk) "
            "RETURN c.uid, c.text, c.chunk_index ORDER BY c.chunk_index",
            u=DOC_UID
        )
        chunks = []
        for r in result:
            chunks.append({"uid": r["c.uid"], "text": r["c.text"], "idx": r["c.chunk_index"]})
        log.info(f"Found {len(chunks)} chunks")
        return chunks

def extract_entities_and_facts(llm, text):
    """Use LLM to extract entities and facts from text."""
    prompt = f"""Extract entities and facts from this text about the DIAL-KG paper.

Return ONLY valid JSON with this exact structure:
{{
  "entities": [
    {{"name": "Entity Name", "type": "PAPER|METHOD|CONCEPT|DATASET|PERSON|METRIC|TASK|SYSTEM"}}
  ],
  "facts": [
    {{"subject": "Entity Name", "relation": "relation_verb", "object": "Entity Name"}}
  ]
}}

Rules:
- entities: unique named things. Use their full canonical name.
- facts: subject-relation-object triples explicitly stated in the text.
- relation: use lowercase with underscores, e.g. "proposes", "achieves", "uses", "improves_upon"
- Include at most 15 entities and 10 facts per chunk.
- If no entities found, return {{"entities": [], "facts": []}}

Text:
{text[:1500]}"""

    try:
        resp = llm.chat.completions.create(
            model=EXTRACT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=1024
        )
        content = resp.choices[0].message.content.strip()
        # Extract JSON from response (handle markdown-wrapped JSON)
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            entities = data.get("entities", [])
            facts = data.get("facts", [])
            # Deduplicate entities by name
            seen = set()
            unique_entities = []
            for e in entities:
                if e["name"] not in seen:
                    seen.add(e["name"])
                    unique_entities.append(e)
            return unique_entities, facts
        else:
            return [], []
    except Exception as ex:
        log.warning(f"LLM extraction failed: {ex}")
        return [], []

def store_in_neo4j(n4j, chunk_uid, entities, facts):
    """Store extracted entities and facts in Neo4j."""
    with n4j.session() as s:
        for ent in entities:
            eid = f"ent-{uuid.uuid4().hex[:8]}"
            s.run(
                """MERGE (e:Entity {name: $n})
                   SET e.type = $t, e.uid = COALESCE(e.uid, $u)""",
                n=ent["name"], t=ent["type"], u=eid
            )
            # Link chunk to entity
            s.run(
                """MATCH (c:Chunk {uid: $cu}), (e:Entity {name: $en})
                   MERGE (c)-[:MENTIONS]->(e)""",
                cu=chunk_uid, en=ent["name"]
            )
        
        for fact in facts:
            fid = f"fact-{uuid.uuid4().hex[:8]}"
            # Create fact node
            s.run(
                "MERGE (f:Fact {uid: $u}) SET f.text = $t",
                u=fid, t=f"{fact['subject']} {fact['relation']} {fact['object']}"
            )
            # Link chunk to fact
            s.run(
                "MATCH (c:Chunk {uid: $cu}), (f:Fact {uid: $fu}) MERGE (c)-[:CONTAINS_FACT]->(f)",
                cu=chunk_uid, fu=fid
            )
            # Link subject entity
            s.run(
                """MATCH (f:Fact {uid: $fu}), (s:Entity {name: $sn})
                   MERGE (f)-[:HAS_SUBJECT]->(s)""",
                fu=fid, sn=fact["subject"]
            )
            # Link object entity
            s.run(
                """MATCH (f:Fact {uid: $fu}), (o:Entity {name: $on})
                   MERGE (f)-[:HAS_OBJECT]->(o)""",
                fu=fid, on=fact["object"]
            )
            # Create RELATED_TO between subject and object entities
            s.run(
                """MATCH (s:Entity {name: $sn}), (o:Entity {name: $on})
                   MERGE (s)-[:RELATED_TO]-(o)""",
                sn=fact["subject"], on=fact["object"]
            )

def main():
    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")
    
    chunks = get_chunks(n4j)
    total_entities = 0
    total_facts = 0
    
    for i, chunk in enumerate(chunks):
        log.info(f"Processing chunk {i+1}/{len(chunks)} ({chunk['uid']})")
        entities, facts = extract_entities_and_facts(llm, chunk["text"])
        if entities or facts:
            store_in_neo4j(n4j, chunk["uid"], entities, facts)
            total_entities += len(entities)
            total_facts += len(facts)
            log.info(f"  → {len(entities)} entities, {len(facts)} facts")
    
    log.info(f"DONE: {total_entities} entities, {total_facts} facts extracted")
    
    # Verify
    with n4j.session() as s:
        result = s.run(
            "MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c)-[:MENTIONS]->(e) "
            "RETURN COUNT(DISTINCT e) AS entities, COUNT(DISTINCT c) AS chunks_with_entities",
            u=DOC_UID
        )
        for r in result:
            log.info(f"Graph: {r['entities']} entities linked to {r['chunks_with_entities']} chunks")
        
        result = s.run(
            "MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c)-[:CONTAINS_FACT]->(f) "
            "RETURN COUNT(DISTINCT f) AS facts",
            u=DOC_UID
        )
        for r in result:
            log.info(f"Graph: {r['facts']} fact nodes")
    
    n4j.close()

if __name__ == "__main__":
    main()