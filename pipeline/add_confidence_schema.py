#!/usr/bin/env python3
"""Add multi-dimensional confidence to Fact and NewsArticle nodes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS
from neo4j import GraphDatabase

n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

with n4j.session() as s:
    # Add confidence dimensions to Fact nodes
    result = s.run("""
        MATCH (f:Fact)
        WHERE f.confidence_strategic IS NULL
        SET f.confidence_evidence_quality = coalesce(f.confidence, 0.5),
            f.confidence_source_reliability = coalesce(f.confidence, 0.5),
            f.confidence_logical_consistency = coalesce(f.confidence, 0.5),
            f.confidence_recency = 1.0,
            f.confidence_strategic = coalesce(f.confidence, 0.5)
        RETURN count(f) AS updated
    """)
    print(f"Updated {result.single()['updated']} Fact nodes with confidence dimensions")

    # Add claim fields to NewsArticle nodes
    result = s.run("""
        MATCH (n:NewsArticle)
        WHERE n.claims IS NULL
        SET n.claims = [],
            n.claim_count = 0,
            n.claim_confidences = []
        RETURN count(n) AS updated
    """)
    print(f"Updated {result.single()['updated']} NewsArticle nodes with claim fields")

    # Show updated schema
    result = s.run("""
        CALL db.schema.nodeTypeProperties()
        YIELD nodeLabels, propertyName, propertyTypes
        WHERE nodeLabels IN ['Fact', 'NewsArticle']
        RETURN nodeLabels, collect(DISTINCT propertyName) AS props
    """)
    for row in result:
        props = [p for p in sorted(row['props'])]
        print(f"\n{row['nodeLabels']} ({len(props)} properties):")
        for p in props:
            print(f"  - {p}")

n4j.close()
print("Done.")
