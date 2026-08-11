#!/usr/bin/env python3
"""Verify commentator graph in Neo4j."""
import sys
from pathlib import Path
from neo4j import GraphDatabase
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS

n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
with n4j.session() as s:
    r = s.run("MATCH (p:Person) WHERE p.role = 'Commentator' RETURN p.name, p.updated_at")
    print("=== COMMENTATORS ===")
    for row in r:
        print(f"  👤 {row['p.name']}")

    r = s.run("""
        MATCH (p:Person {role: 'Commentator'})<-[:MENTIONS]-(n:NewsArticle)
        OPTIONAL MATCH (n)-[:CONTAINS_CLAIM]->(c:Claim)
        RETURN p.name, count(DISTINCT n) AS articles, count(c) AS claims
        ORDER BY articles DESC
    """)
    print("\n=== ARTICLES & CLAIMS PER PERSON ===")
    for row in r:
        print(f"  {row['p.name']:20s} | {row['articles']} articles | {row['claims']} claims")

    r = s.run("""
        MATCH (p:Person {role: 'Commentator'})<-[:MENTIONS]-(n:NewsArticle)-[:CONTAINS_CLAIM]->(c:Claim)
        RETURN p.name, n.title, c.text, c.confidence LIMIT 10
    """)
    print("\n=== SAMPLE CLAIMS ===")
    for row in r:
        print(f"  {row['p.name']}: {row['c.text'][:70]} [{row['c.confidence']:.0%}]")

n4j.close()