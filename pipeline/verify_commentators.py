#!/usr/bin/env python3
"""Verify commentator graph in Neo4j."""
from neo4j import GraphDatabase
n4j = GraphDatabase.driver("bolt://192.168.0.114:7687", auth=("neo4j", "Erna#26neo4j"))
with n4j.session() as s:
    # Person nodes
    r = s.run("MATCH (p:Person) WHERE p.role = 'Commentator' RETURN p.name, p.updated_at")
    print("=== COMMENTATOR PERSONS ===")
    for row in r:
        print(f"  👤 {row['p.name']}")

    # Articles per person
    r = s.run("""
        MATCH (p:Person {role: 'Commentator'})<-[:MENTIONS]-(n:NewsArticle)
        OPTIONAL MATCH (n)-[:CONTAINS_CLAIM]->(c:Claim)
        RETURN p.name, count(DISTINCT n) AS articles, count(c) AS claims
        ORDER BY articles DESC
    """)
    print("\n=== ARTICLES & CLAIMS PER PERSON ===")
    for row in r:
        print(f"  {row['p.name']:20s} | {row['articles']} articles | {row['claims']} claims")

    # Sample claims
    r = s.run("""
        MATCH (p:Person {role: 'Commentator'})<-[:MENTIONS]-(n:NewsArticle)-[:CONTAINS_CLAIM]->(c:Claim)
        RETURN p.name, n.title, c.text, c.confidence
        LIMIT 10
    """)
    print("\n=== SAMPLE CLAIMS ===")
    for row in r:
        print(f"  {row['p.name']}: {row['c.text'][:70]} [{row['c.confidence']:.0%}]")

n4j.close()