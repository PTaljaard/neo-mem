"""Alternative: embedding-based conceptualization (no LLM required).
Avoids gemma4's safety filter on categorization prompts.

We embed each entity name, then find the nearest concept by
looking at pre-defined concept clusters via embedding similarity.

This is simpler and free (no LLM calls), but less rich than
AutoSchemaKG's LLM-based approach.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE, EMBED_MODEL
from neo4j import GraphDatabase
from openai import OpenAI
import numpy as np

# Seed concept hierarchy — abstract categories for KG entities.
# AutoSchemaKG uses LLM to generate these dynamically.
# We seed known categories and use embedding similarity to assign.
# Ref: AutoSchemaKG conceptualization (Bai et al., ACL 2026)
SEED_CONCEPTS = {
    # Entity-level concepts
    "system": ["system", "platform", "framework", "tool", "software", "application"],
    "method": ["method", "technique", "approach", "algorithm", "procedure", "strategy"],
    "task": ["task", "objective", "goal", "problem", "challenge", "benchmark"],
    "concept": ["concept", "idea", "notion", "principle", "theory", "paradigm"],
    "dataset": ["dataset", "corpus", "collection", "data", "resource", "benchmark"],
    "paper": ["paper", "publication", "article", "document", "research", "study"],
    "person": ["person", "individual", "researcher", "author", "scientist"],
    "organization": ["organization", "institution", "company", "lab", "group", "agency"],
    "metric": ["metric", "measure", "score", "evaluation", "indicator", "performance"],
    "model": ["model", "architecture", "network", "classifier", "embedding"],
    "language": ["language", "programming language", "natural language"],
    "hardware": ["hardware", "device", "machine", "processor", "computer"],
    "location": ["location", "place", "region", "country", "city", "area"],
    "event": ["event", "occurrence", "incident", "situation", "development"],
    "scenario": ["scenario", "situation", "context", "environment", "condition"],
    "theory": ["theory", "framework", "paradigm", "discipline", "field", "domain"],
    "technology": ["technology", "innovation", "invention", "discovery", "advancement"],
    "process": ["process", "pipeline", "workflow", "procedure", "operation", "phase"],
    "data": ["data", "information", "knowledge", "content", "evidence"],
    "standard": ["standard", "protocol", "specification", "guideline", "rule"],
}

# Second-level concepts (broader abstractions)
HIGHER_CONCEPTS = {
    "technology": ["technology", "innovation", "engineering"],
    "science": ["science", "research", "knowledge", "academia"],
    "organization": ["organization", "institution", "collective"],
    "abstraction": ["abstraction", "concept", "idea", "theory"],
    "resource": ["resource", "data", "corpus", "dataset", "tool"],
    "artificial_intelligence": ["AI", "artificial intelligence", "machine learning", "deep learning", "NLP"],
    "economics": ["economics", "economy", "finance", "market", "trade"],
    "politics": ["politics", "government", "policy", "law", "governance"],
    "military": ["military", "defense", "warfare", "conflict", "security"],
    "geography": ["geography", "location", "region", "country", "continent"],
}


def embed_texts(llm, texts):
    """Batch embed texts using nomic-embed-text."""
    resp = llm.embeddings.create(input=texts, model=EMBED_MODEL)
    return [d.embedding for d in resp.data]


def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def main():
    import argparse
    p = argparse.ArgumentParser(description="Build concept hierarchy (embedding-based)")
    p.add_argument("--limit", type=int, default=200, help="Max entities to process")
    p.add_argument("--threshold", type=float, default=0.35, help="Similarity threshold")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")

    # 1. Embed all seed concepts
    concept_names = list(SEED_CONCEPTS.keys())
    concept_terms = [term for terms in SEED_CONCEPTS.values() for term in terms]
    all_embeddings = embed_texts(llm, concept_terms)
    # Average embeddings per concept
    concept_embeddings = {}
    idx = 0
    for cname in concept_names:
        n_terms = len(SEED_CONCEPTS[cname])
        avg_emb = np.mean([all_embeddings[idx + i] for i in range(n_terms)], axis=0)
        concept_embeddings[cname] = avg_emb / np.linalg.norm(avg_emb)
        idx += n_terms

    # Also embed higher concepts
    higher_names = list(HIGHER_CONCEPTS.keys())
    higher_terms = [term for terms in HIGHER_CONCEPTS.values() for term in terms]
    higher_embs = embed_texts(llm, higher_terms)
    higher_embeddings = {}
    idx = 0
    for hname in higher_names:
        n_terms = len(HIGHER_CONCEPTS[hname])
        avg_emb = np.mean([higher_embs[idx + i] for i in range(n_terms)], axis=0)
        higher_embeddings[hname] = avg_emb / np.linalg.norm(avg_emb)
        idx += n_terms

    print(f"📊 Seed: {len(concept_names)} level-1 + {len(higher_names)} level-2 concepts")

    # 2. Fetch entities
    with n4j.session() as s:
        r = s.run("""
            MATCH (e:Entity)
            WHERE NOT (e)-[:IS_A]->(:Concept)
            RETURN e.name, e.type
            LIMIT $limit
        """, limit=args.limit)
        entities = [(row["e.name"], row["e.type"]) for row in r]
    print(f"🔍 Found {len(entities)} entities to conceptualize")

    if args.dry_run:
        n4j.close()
        return

    # 3. Embed entities and match to nearest concept
    entity_names = [e[0] for e in entities]
    entity_embs = embed_texts(llm, entity_names)

    total_concepts = 0
    for (ename, etype), eemb in zip(entities, entity_embs):
        eemb_norm = np.array(eemb) / np.linalg.norm(eemb)

        # Find best matching level-1 concept
        best_concept = None
        best_score = 0
        for cname, cemb in concept_embeddings.items():
            sim = cosine_similarity(eemb_norm, cemb)
            if sim > best_score:
                best_score = sim
                best_concept = cname

        if best_score < args.threshold:
            continue  # No good match

        with n4j.session() as s:
            # Create concept node
            cuid = f"concept-{hash(best_concept) & 0xffffffff:08x}"
            s.run("""
                MERGE (c:Concept {uid: $u})
                SET c.name = $n, c.created_at = datetime(),
                    c.source = 'embedding-conceptualization',
                    c.abstraction_level = 1
            """, u=cuid, n=best_concept)

            # Link entity → concept
            s.run("""
                MATCH (e:Entity {name: $en}), (c:Concept {uid: $cu})
                MERGE (e)-[:IS_A]->(c)
            """, en=ename, cu=cuid)

            # Find best matching higher concept
            best_higher = None
            best_higher_score = 0
            for hname, hemb in higher_embeddings.items():
                sim = cosine_similarity(eemb_norm, hemb)
                if sim > best_higher_score:
                    best_higher_score = sim
                    best_higher = hname

            if best_higher and best_higher_score > args.threshold:
                hcuid = f"concept-{hash(best_higher) & 0xffffffff:08x}"
                s.run("""
                    MERGE (c:Concept {uid: $u})
                    SET c.name = $n, c.created_at = datetime(),
                        c.source = 'embedding-conceptualization',
                        c.abstraction_level = 2
                """, u=hcuid, n=best_higher)

                # Link concept → higher concept
                s.run("""
                    MATCH (lower:Concept {uid: $lu}), (higher:Concept {uid: $hu})
                    MERGE (lower)-[:IS_A]->(higher)
                """, lu=cuid, hu=hcuid)

            total_concepts += 1

        if total_concepts % 50 == 0:
            print(f"  ✅ {total_concepts} entities conceptualized...")

    # 4. Summary
    with n4j.session() as s:
        r = s.run("MATCH (c:Concept) RETURN count(c) AS total").single()
        r2 = s.run("MATCH ()-[r:IS_A]->() RETURN count(r) AS edges").single()
    print(f"\n📊 Summary: {total_concepts} entities → {r['total']} concepts → {r2['edges']} IS_A edges")

    n4j.close()


if __name__ == "__main__":
    main()