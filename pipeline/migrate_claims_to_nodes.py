#!/usr/bin/env python3
"""Migrate claims from NewsArticle arrays to separate :Claim nodes + :CONTAINS_CLAIM edges.
Also adds NewsArticle and Claim to the ontology class hierarchy."""
from neo4j import GraphDatabase

NEO4J_URI = "bolt://192.168.0.114:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "Erna#26neo4j"

n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

with n4j.session() as s:
    # ── 1. Add Claim to ontology class hierarchy ──────────────────────────
    s.run("""
        MERGE (c:OntologyClass {
            class_id: "Claim",
            label: "Claim",
            description: "A factual assertion extracted from a news article. The atomic unit of strategic intelligence.",
            extends: "Upper"
        })
        SET c.updated_at = datetime(),
            c.properties = ["uid", "text", "confidence", "extracted_at", "source_uid"]
    """)
    print("✅ OntologyClass: Claim")

    # Add NewsArticle to ontology
    s.run("""
        MERGE (n:OntologyClass {
            class_id: "NewsArticle",
            label: "NewsArticle",
            description: "A news article ingested from an RSS feed with full text and metadata.",
            extends: "Upper"
        })
        SET n.updated_at = datetime(),
            n.properties = ["uid", "title", "summary", "body", "url", "source", "category",
                           "fetched_at", "published_at", "embedding"]
    """)
    print("✅ OntologyClass: NewsArticle")

    # ── 2. Add Claim-specific properties ─────────────────────────────────
    s.run("MERGE (:Property {key: 'text', description: 'The claim text', datatype: 'string'})")
    s.run("MERGE (:Property {key: 'confidence', description: 'Multi-dimensional or overall confidence score', datatype: 'float'})")
    s.run("MERGE (:Property {key: 'extracted_at', description: 'When the claim was extracted', datatype: 'datetime'})")
    s.run("MERGE (:Property {key: 'source_uid', description: 'UID of the source NewsArticle', datatype: 'string'})")
    print("✅ Properties: text, confidence, extracted_at, source_uid")

    # ── 3. Migrate existing claims from arrays to separate nodes ──────────
    result = s.run("""
        MATCH (n:NewsArticle)
        WHERE size(n.claims) > 0
        RETURN count(n) AS articles_with_claims
    """)
    count = result.single()["articles_with_claims"]
    print(f"Found {count} articles with claim arrays")

    result = s.run("""
        MATCH (n:NewsArticle)
        WHERE size(n.claims) > 0
        UNWIND range(0, size(n.claims)-1) AS i
        MERGE (c:Claim {
            uid: n.uid + "-c" + toString(i)
        })
        SET c.text = n.claims[i],
            c.confidence = n.claim_confidences[i],
            c.extracted_at = n.fetched_at,
            c.source_uid = n.uid
        WITH c, n
        MERGE (n)-[:CONTAINS_CLAIM]->(c)
        RETURN count(DISTINCT c) AS claims_created
    """)
    created = result.single()["claims_created"]
    print(f"✅ Created {created} Claim nodes with CONTAINS_CLAIM edges")

    # ── 4. Verify ────────────────────────────────────────────────────────
    r = s.run("""
        MATCH (n:NewsArticle)-[:CONTAINS_CLAIM]->(c:Claim)
        RETURN count(DISTINCT n) AS articles, count(c) AS claims
    """)
    for row in r:
        print(f"✅ Verification: {row['articles']} articles linked to {row['claims']} claims")
    
    r = s.run("MATCH (c:Claim) RETURN count(c) AS total")
    print(f"Total Claim nodes: {r.single()['total']}")

n4j.close()