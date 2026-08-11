#!/usr/bin/env python3
"""Save the Amber heat claims to Neo4j as Claim nodes."""
from neo4j import GraphDatabase
n4j = GraphDatabase.driver("bolt://192.168.0.114:7687", auth=("neo4j", "Erna#26neo4j"))
with n4j.session() as s:
    r = s.run("MATCH (n:NewsArticle) WHERE size(n.body) > 100 AND (n.claim_count IS NULL OR n.claim_count = 0) RETURN n.uid, n.title LIMIT 1")
    row = r.single()
    if row:
        uid = row["n.uid"]
        title = row["n.title"]
        claims = [
            "Amber heat alerts are active across most of England until Friday evening.",
            "The UKHSA warns that the heatwave may cause a rise in deaths.",
            "71.3% of England is currently experiencing drought conditions."
        ]
        confs = [0.95, 0.92, 0.98]
        for i, (c, cf) in enumerate(zip(claims, confs)):
            s.run("MERGE (c:Claim {uid: $u}) SET c.text = $t, c.confidence = $cf, c.extracted_at = datetime(), c.source_uid = $su",
                  u=f"{uid}-c{i}", t=c, cf=cf, su=uid)
            s.run("MATCH (n:NewsArticle {uid: $nu}), (c:Claim {uid: $cu}) MERGE (n)-[:CONTAINS_CLAIM]->(c)",
                  nu=uid, cu=f"{uid}-c{i}")
        s.run("MATCH (n:NewsArticle {uid: $u}) SET n.claim_count = 3", u=uid)
        print(f"✅ Saved 3 claims for: {title[:50]}")
    else:
        print("No articles needing claims found")
n4j.close()