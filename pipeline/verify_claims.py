#!/usr/bin/env python3
"""Verify Claim node structure in Neo4j."""
import sys
from pathlib import Path
from neo4j import GraphDatabase
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS

n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
errors = []

def check(desc, cond, detail=""):
    if cond:
        print(f"  ✅ {desc}")
    else:
        print(f"  ❌ {desc} — {detail}")
        errors.append(desc)

with n4j.session() as s:
    # 1. Claim nodes exist
    r = s.run("MATCH (c:Claim) RETURN count(c) AS n").single()
    check("Claim nodes exist", r["n"] > 0, f"found {r['n']}")

    # 2. Claim properties
    r = s.run("MATCH (c:Claim) WHERE c.text IS NOT NULL AND c.confidence IS NOT NULL AND c.source_uid IS NOT NULL RETURN count(c) AS valid").single()
    r2 = s.run("MATCH (c:Claim) RETURN count(c) AS total").single()
    check(f"All {r2['total']} Claim nodes have text + confidence + source_uid", r["valid"] == r2["total"], f"{r['valid']}/{r2['total']}")

    # 3. CONTAINS_CLAIM edges
    r = s.run("MATCH (n:NewsArticle)-[:CONTAINS_CLAIM]->(c:Claim) RETURN count(DISTINCT n) AS arts, count(c) AS claims").single()
    check("CONTAINS_CLAIM edges connect articles to claims", r["arts"] > 0, f"{r['arts']} articles → {r['claims']} claims")

    # 4. No legacy array claims
    r = s.run("MATCH (n:NewsArticle) WHERE n.claims IS NOT NULL RETURN count(n) AS n").single()
    check("No legacy array claims on NewsArticle", r["n"] == 0, f"{r['n']} still have arrays")

    # 5. OntologyClass registered
    r = s.run("MATCH (o:OntologyClass {class_id: 'Claim'}) RETURN o").single()
    check("OntologyClass 'Claim' registered", r is not None)

    # 6. NewsArticle links to Person
    r = s.run("MATCH (p:Person {role: 'Commentator'})<-[:MENTIONS]-(n:NewsArticle) RETURN count(DISTINCT p) AS persons, count(n) AS articles").single()
    check(f"Commentator graph: {r['persons']} persons linked to articles", r['persons'] > 0, f"{r['persons']} persons, {r['articles']} articles")

    # 7. NewsArticle with Claim nodes
    r = s.run("MATCH (n:NewsArticle)-[:CONTAINS_CLAIM]->(c:Claim) RETURN count(DISTINCT n) AS arts").single()
    check(f"Articles with claims: {r['arts']}", r['arts'] > 0)

    # 8. Total counts
    r = s.run("MATCH (n:NewsArticle) RETURN count(n) AS total").single()
    r2 = s.run("MATCH (c:Claim) RETURN count(c) AS total").single()
    r3 = s.run("MATCH (p:Person {role: 'Commentator'}) RETURN count(p) AS total").single()
    print(f"\n  📊 Total: {r['total']} NewsArticles, {r2['total']} Claims, {r3['total']} Commentators")

n4j.close()
print(f"\n{'='*40}")
if errors:
    print(f"❌ {len(errors)} check(s) FAILED:")
    for e in errors:
        print(f"   - {e}")
    sys.exit(1)
else:
    print("✅ All checks passed — pipeline is healthy.")
    sys.exit(0)