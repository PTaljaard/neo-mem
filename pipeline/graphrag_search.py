#!/usr/bin/env python3
"""
Semantic search + graph traversal pipeline.
Embeds a query, finds top-K chunks by vector similarity,
then traverses the graph 1-3 hops for context.

Usage:
    python3 graphrag_search.py "HippoRAG2 personalized PageRank"
    python3 graphrag_search.py "governance in knowledge graph construction" --hops 3
"""
import sys, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE, EMBED_MODEL
from neo4j import GraphDatabase
from openai import OpenAI

log = print


def embed_query(llm, query):
    """Embed a text query using nomic-embed-text."""
    resp = llm.embeddings.create(input=query, model=EMBED_MODEL)
    return resp.data[0].embedding


def vector_search(n4j, query_embedding, top_k=10):
    """Semantic search: find top-K chunks by cosine similarity."""
    with n4j.session() as s:
        r = s.run("""
            CALL db.index.vector.queryNodes('chunk_embedding_index', $k, $emb)
            YIELD node AS chunk, score
            RETURN chunk.uid AS uid, chunk.text AS text, 
                   chunk.doc_id AS doc_id, score
            ORDER BY score DESC
        """, k=top_k, emb=query_embedding)
        return [(row["uid"], row["text"], row["doc_id"], row["score"]) for row in r]


def graph_traverse(n4j, chunk_uids, max_hops=3):
    """Traverse the graph from seed chunks up to max_hops."""
    context = {
        "entities": set(),
        "events": set(),
        "facts": set(),
        "claims": set(),
        "event_relations": [],
        "concepts": set(),
    }
    
    with n4j.session() as s:
        for cuid in chunk_uids:
            # 1-hop: Entities mentioned in these chunks
            r = s.run("""
                MATCH (c:Chunk {uid: $u})-[:MENTIONS]->(e:Entity)
                RETURN e.name AS name, e.type AS type
            """, u=cuid)
            for row in r:
                context["entities"].add((row["name"], row["type"]))
            
            # 1-hop: Events in these chunks
            r = s.run("""
                MATCH (c:Chunk {uid: $u})<-[:MENTIONS]-(e:Event)
                RETURN e.name AS name, e.description AS desc
            """, u=cuid)
            for row in r:
                context["events"].add((row["name"], row["desc"]))
            
            # 1-hop: Facts in these chunks
            r = s.run("""
                MATCH (c:Chunk {uid: $u})-[:CONTAINS_FACT]->(f:Fact)
                RETURN f.text AS text, f.confidence AS conf
            """, u=cuid)
            for row in r:
                context["facts"].add((row["text"], row["conf"]))
            
            # 1-hop: Claims in linked news articles
            r = s.run("""
                OPTIONAL MATCH (n:NewsArticle)-[:CONTAINS_CLAIM]->(cl:Claim)
                WHERE n.body CONTAINS $u OR n.uid = $u
                RETURN cl.text AS text, cl.confidence AS conf
                LIMIT 5
            """, u=cuid)
            for row in r:
                if row["text"]:
                    context["claims"].add((row["text"], row["conf"]))
            
            # 2-hop: Event-Event relations
            r = s.run("""
                MATCH (c:Chunk {uid: $u})<-[:MENTIONS]-(e1:Event)-[r:CAUSES|BEFORE|AFTER|RESULTS_IN|SIMULTANEOUS]->(e2:Event)
                RETURN e1.description AS head, type(r) AS rel, e2.description AS tail
            """, u=cuid)
            for row in r:
                context["event_relations"].append((row["head"], row["rel"], row["tail"]))
            
            # 2-hop: Concepts linked to entities
            r = s.run("""
                MATCH (c:Chunk {uid: $u})-[:MENTIONS]->(e:Entity)-[:IS_A]->(con:Concept)
                RETURN con.name AS concept, con.abstraction_level AS level
            """, u=cuid)
            for row in r:
                context["concepts"].add((row["concept"], row["level"]))
            
            # 3-hop: VV relations from events to other events (chained)
            if max_hops >= 3:
                r = s.run("""
                    MATCH (c:Chunk {uid: $u})<-[:MENTIONS]-(e1:Event)-[:CAUSES|BEFORE|AFTER|RESULTS_IN]->(e2:Event)
                    WHERE NOT (e2)<-[:MENTIONS]-(c)
                    RETURN e2.description AS desc, '2-hop-event' AS source
                    LIMIT 10
                """, u=cuid)
                for row in r:
                    context["events"].add((row["desc"], row["source"]))
    
    return context


def format_context(query, chunks, context):
    """Format the search results into a readable report."""
    lines = []
    lines.append(f"🔍 SEMANTIC SEARCH: \"{query}\"")
    lines.append(f"{'=' * 65}")
    lines.append(f"Found {len(chunks)} relevant chunks\n")
    
    for i, (uid, text, doc_id, score) in enumerate(chunks[:5], 1):
        lines.append(f"--- Chunk {i} (score={score:.4f}, doc={doc_id}) ---")
        # Show first 300 chars of the chunk
        lines.append(text[:300])
        lines.append("")
    
    if context["entities"]:
        lines.append(f"\n📦 ENTITIES ({len(context['entities'])})")
        for name, etype in sorted(context["entities"])[:15]:
            lines.append(f"  {name:40s} [{etype}]")
    
    if context["events"]:
        lines.append(f"\n📅 EVENTS ({len(context['events'])})")
        for name, desc in sorted(context["events"])[:15]:
            lines.append(f"  • {desc or name}")
    
    if context["event_relations"]:
        lines.append(f"\n🔗 EVENT-EVENT RELATIONS ({len(context['event_relations'])})")
        for head, rel, tail in context["event_relations"][:15]:
            lines.append(f"  {str(head)[:50]:50s} --{rel}--> {str(tail)[:50]}")
    
    if context["concepts"]:
        lines.append(f"\n🏛️  CONCEPTS ({len(context['concepts'])})")
        for name, level in sorted(context["concepts"]):
            lines.append(f"  L{level} {name}")
    
    if context["facts"]:
        lines.append(f"\n📋 FACTS ({len(context['facts'])})")
        for text, conf in list(context["facts"])[:10]:
            lines.append(f"  [{conf:.0%}] {text[:80]}")
    
    return "\n".join(lines)


def main():
    import argparse
    p = argparse.ArgumentParser(description="Semantic search + graph traversal")
    p.add_argument("query", help="Search query")
    p.add_argument("--top-k", type=int, default=10, help="Number of chunks to retrieve")
    p.add_argument("--hops", type=int, default=3, help="Max graph traversal hops")
    args = p.parse_args()

    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")

    # 1. Embed query
    log(f"🔤 Embedding query: '{args.query}'")
    emb = embed_query(llm, args.query)
    log(f"   {len(emb)} dims")

    # 2. Vector search
    chunks = vector_search(n4j, emb, args.top_k)
    log(f"🔍 Found {len(chunks)} chunks by vector similarity")

    # 3. Graph traversal
    chunk_uids = [c[0] for c in chunks if c[0]]
    context = graph_traverse(n4j, chunk_uids, args.hops)

    # 4. Print results
    output = format_context(args.query, chunks, context)
    print(output)

    n4j.close()


if __name__ == "__main__":
    main()