#!/usr/bin/env python3
"""
HippoRAG2-style personalized PageRank retrieval for Neo4j.
Uses pure Cypher (no GDS dependency) — runs on any Neo4j.

Algorithm:
1. Find seed entities from the query via LLM NER
2. Walk the graph 1-3 hops from seeds
3. Score each node by: proximity + frequency + connection density
4. Return top-N passages with context

Usage:
    python3 hipporag_retrieve.py "HippoRAG2 personalized PageRank" --top-k 10
    python3 hipporag_retrieve.py "governance in knowledge graphs" --hops 3
"""
import sys, json, re, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE, CHAT_MODEL, EMBED_MODEL
from neo4j import GraphDatabase
from openai import OpenAI

log = print


def extract_entities(llm, query):
    """Extract key entities from the query. Falls back to keyword extraction if LLM fails."""
    # Try LLM NER first
    prompt = f"""Extract the key entities from this query. Return ONLY a JSON array of strings.
    No explanations, no markdown.
    ["entity1", "entity2"]
    Query: {query}"""
    try:
        resp = llm.chat.completions.create(
            model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=128
        )
        raw = resp.choices[0].message.content or "[]"
        raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
        raw = re.sub(r'\s*```', '', raw)
        s, e = raw.find('['), raw.rfind(']')
        if s >= 0 and e > s:
            entities = json.loads(raw[s:e+1])
            if entities:
                return entities
    except:
        pass
    
    # Fallback: extract capitalized multi-word phrases from query
    words = re.findall(r'[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)*', query)
    if words:
        return words[:3]  # limit to 3 entities
    
    # Fallback: use significant words from query
    sig_words = [w for w in query.split() if len(w) > 3 and w[0].isupper()]
    if sig_words:
        return sig_words[:3]
    
    # Last resort: use the whole query as a single entity
    return [query.strip()]


def find_seed_entities(n4j, entity_names, llm=None, query=""):
    """Find seed entities in the graph. Falls back to vector search + NER from top chunks."""
    seeds = []
    with n4j.session() as s:
        for name in entity_names:
            r = s.run("""
                MATCH (e:Entity)
                WHERE toLower(e.name) CONTAINS toLower($name)
                RETURN e.name, e.type, e.uid
                LIMIT 5
            """, name=name)
            for row in r:
                seeds.append((row["e.name"], row["e.type"], row["e.uid"]))
    
    if seeds:
        return seeds
    
    # Fallback: embed the query and find top chunks, use those as seeds
    if llm and query:
        emb = embed_query(llm, query)
        with n4j.session() as s:
            r = s.run("""
                CALL db.index.vector.queryNodes('chunk_embedding_index', 5, $emb)
                YIELD node AS chunk, score
                RETURN chunk.uid AS uid, chunk.text AS text, score
                ORDER BY score DESC
            """, emb=emb)
            for row in r:
                # Return as pseudo-entities with type CHUNK_SEED
                seeds.append((row["uid"], "CHUNK_SEED", row["uid"]))
    
    return seeds


def embed_query(llm, query):
    from pipeline_config import EMBED_MODEL
    resp = llm.embeddings.create(input=query, model=EMBED_MODEL)
    return resp.data[0].embedding


def personalized_pagerank(n4j, seed_uids, seed_types, hops=3, top_k=10):
    """
    Simplified personalized PageRank using pure Cypher.
    
    For each seed entity, walk the graph up to `hops` deep.
    Score each visited node by:
      - hop_distance: 1.0 at hop 1, 0.5 at hop 2, 0.25 at hop 3
      - edge_count: how many paths lead to this node
      - degree: node's connectivity
    """
    scored = {}
    
    with n4j.session() as s:
        for seed_type, seed_uid in zip(seed_types, seed_uids):
            label = "Entity" if seed_type == "Entity" else "Chunk"
            qlbl = f":{label}"
            
            # Walk 1-hop
            r = s.run(f"""
                MATCH (seed{qlbl} {{uid: $uid}})-[r]-(n)
                RETURN n.uid AS uid, labels(n) AS labels, 
                       type(r) AS rel, 'hop1' AS hop
                LIMIT 50
            """, uid=seed_uid)
            for row in r:
                uid = row["uid"]
                if uid == seed_uid:
                    continue
                score = 1.0  # hop 1 weight
                scored[uid] = scored.get(uid, 0) + score
            
            if hops >= 2:
                # Walk 2-hop
                r = s.run(f"""
                    MATCH (seed{qlbl} {{uid: $uid}})-[r1]-(n1)-[r2]-(n2)
                    WHERE n2.uid <> $uid
                    RETURN n2.uid AS uid, labels(n2) AS labels, count(*) AS paths
                    LIMIT 100
                """, uid=seed_uid)
                for row in r:
                    uid = row["uid"]
                    if uid == seed_uid:
                        continue
                    score = 0.5 * row["paths"]  # hop 2 weight * path count
                    scored[uid] = scored.get(uid, 0) + score
            
            if hops >= 3:
                # Walk 3-hop
                r = s.run(f"""
                    MATCH (seed{qlbl} {{uid: $uid}})-[r1]-(n1)-[r2]-(n2)-[r3]-(n3)
                    WHERE n3.uid <> $uid AND n3.uid <> n1.uid
                    RETURN n3.uid AS uid, labels(n3) AS labels, count(*) AS paths
                    LIMIT 100
                """, uid=seed_uid)
                for row in r:
                    uid = row["uid"]
                    score = 0.25 * row["paths"]  # hop 3 weight * path count
                    scored[uid] = scored.get(uid, 0) + score
    
    # Sort by score descending
    ranked = sorted(scored.items(), key=lambda x: -x[1])
    
    # For each ranked node, get its text content
    results = []
    with n4j.session() as s:
        for uid, score in ranked[:top_k]:
            r = s.run("""
                OPTIONAL MATCH (n:Chunk {uid: $uid})
                OPTIONAL MATCH (n:NewsArticle {uid: $uid})
                OPTIONAL MATCH (n:Fact {uid: $uid})
                WITH coalesce(n.text, n.body, n.triple_text) AS text,
                     labels(n) AS labels, n.uid AS uid
                RETURN uid, labels, substring(text, 0, 300) AS snippet
                LIMIT 1
            """, uid=uid)
            row = r.single()
            if row and row["snippet"]:
                results.append({
                    "uid": uid,
                    "labels": row["labels"],
                    "snippet": row["snippet"],
                    "score": score,
                })
            elif row:
                # Not a text-bearing node, show what it is
                results.append({
                    "uid": uid,
                    "labels": row["labels"],
                    "snippet": f"[{row['labels'][0] if row['labels'] else '?'}]",
                    "score": score,
                })
    
    return results, ranked


def main():
    import argparse
    p = argparse.ArgumentParser(description="HippoRAG2-style personalized PageRank retrieval")
    p.add_argument("query", help="Search query")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--hops", type=int, default=3)
    args = p.parse_args()

    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")

    # 1. NER: extract entities from query
    print(f"🔍 Query: '{args.query}'")
    entities = extract_entities(llm, args.query)
    print(f"📝 Extracted entities: {entities}")

    if not entities:
        print("❌ No entities extracted. Falling back to semantic search.")
        n4j.close()
        sys.exit(1)

    # 2. Find seed entities in graph
    seeds = find_seed_entities(n4j, entities, llm, args.query)
    print(f"🎯 Seed entities found: {[(s[0], s[1]) for s in seeds]}")

    if not seeds:
        print("❌ No seed entities found in graph. Falling back to semantic search.")
        n4j.close()
        sys.exit(1)

    # 3. Personalized PageRank
    seed_uids = [s[2] for s in seeds]
    seed_types = [s[1] for s in seeds]
    results, ranked = personalized_pagerank(n4j, seed_uids, seed_types, args.hops, args.top_k)

    print(f"\n{'='*60}")
    print(f"PERSONALIZED PAGERANK RESULTS ({len(results)} nodes)")
    print(f"{'='*60}")
    for r in results:
        score_bar = "█" * min(int(r["score"] * 2), 20)
        print(f"\n  [{r['score']:.2f}] {score_bar}")
        print(f"  {r['uid']} [{r['labels'][0] if r['labels'] else '?'}]")
        print(f"  {r['snippet'][:200]}")
    print()

    # 4. Also show the seed-to-result path
    print(f"\n{'='*60}")
    print(f"SEED → RESULT PATHS")
    print(f"{'='*60}")
    for seed_name, seed_type, seed_uid in seeds[:3]:
        with n4j.session() as s:
            r = s.run("""
                MATCH path = (seed {uid: $uid})-[*1..2]-(n)
                WHERE n.uid IN $result_uids
                RETURN n.uid AS target, length(path) AS hops
                LIMIT 5
            """, uid=seed_uid, result_uids=[ru["uid"] for ru in results[:5]])
            for row in r:
                print(f"  {seed_name} --{row['hops']}hop--> {row['target']}")

    n4j.close()


if __name__ == "__main__":
    main()