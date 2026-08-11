#!/usr/bin/env python3
"""Query Neo4j for today's news articles with their Claim sub-nodes."""
from neo4j import GraphDatabase
n4j = GraphDatabase.driver("bolt://192.168.0.114:7687", auth=("neo4j", "Erna#26neo4j"))
with n4j.session() as s:
    r = s.run("""
        MATCH (n:NewsArticle)
        WHERE n.fetched_at >= datetime() - duration('P1D')
        WITH n ORDER BY n.fetched_at DESC
        OPTIONAL MATCH (n)-[:CONTAINS_CLAIM]->(c:Claim)
        RETURN n.title, n.source, n.category, n.claim_count, 
               collect(c.text) AS claims, collect(c.confidence) AS confs
    """)
    articles = []
    for row in r:
        t = row["n.title"] or "(no title)"
        src = row["n.source"] or "?"
        cat = row["n.category"] or "?"
        cc = row["n.claim_count"] or 0
        claims = row["claims"] or []
        confs = row["confs"] or []
        articles.append({"title": t, "source": src, "cat": cat, "cc": cc, "claims": claims, "confs": confs})

    print(f"=== TODAY'S ARTICLES: {len(articles)} ===")
    for a in articles:
        print(f"\n📰 {a['title'][:80]}")
        print(f"   Source: {a['source']} | Category: {a['cat']} | Claims: {a['cc']}")
        for i, (c, cf) in enumerate(zip(a['claims'], a['confs'])):
            bar = "🟢" if cf >= 0.7 else "🟡" if cf >= 0.4 else "🔴"
            print(f"   {i+1}. {bar} [{cf:.0%}] {c[:80]}")

    r = s.run("MATCH (n:NewsArticle)-[:CONTAINS_CLAIM]->(c:Claim) RETURN count(DISTINCT n) AS articles, count(c) AS claims")
    for row in r:
        print(f"\n=== KB: {row['articles']} articles with {row['claims']} Claim nodes ===")
    
    r = s.run("MATCH (c:Claim) RETURN count(c) AS total_claims")
    print(f"Total Claim nodes in graph: {r.single()['total_claims']}")

n4j.close()