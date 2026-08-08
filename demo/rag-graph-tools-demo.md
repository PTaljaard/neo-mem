# Demo: RAG + GraphRAG + Tools — Why Graph Beats Flat Retrieval
## Hermes Agent script for the PhD Hypothesis

### Purpose
Demonstrate that **vector RAG alone is insufficient** — graphs reveal **hidden connections**
between concepts that no chunk-ranking algorithm can find, and web tools **verify and
ground** the graph in real-world evidence.

### Hypothesis (for the PhD)
> Semantic vector search retrieves *what* is relevant.
> Graph traversal reveals *why* things are connected.
> Web tools verify *what is actually true right now*.
> Together, they prove that RAG without graphs is blind to structure,
> and graphs without tools is blind to the present.

---

## Question

```
"In the paper 2302.00093, what problems are discussed, what systems or datasets
are proposed to solve them, and how do those solutions relate to the ethical values
of Fairness and Respect? Then check: have any of those proposed systems been
deployed in real-world healthcare since publication?"
```

---

## Step-by-step Hermes Execution Plan

### Step 1 — Vector RAG: Find relevant chunks

```cypher
// Semantic search over chunks from the paper
CALL db.index.vector.queryNodes('chunk_embedding_index', 5, $query_vector)
YIELD node, score
MATCH (node)-[:BELONGS_TO]->(d:Document {title: "2302.00093"})
OPTIONAL MATCH (node)-[:MENTIONS]->(entity)
RETURN substring(node.content, 0, 200) AS chunk,
       score,
       collect(DISTINCT labels(entity)[0] + ': ' + coalesce(entity.name, entity.label)) AS entities
```

**What RAG gives you:**
- The 5 most semantically similar chunks from the paper
- The entities mentioned in those chunks
- A flat list of "paper chunk 12 talks about Problem X"

**What RAG misses:**
- That Problem X is also linked to Problem Y through a shared System
- That the paper's approach relates to Fairness (an ethical value) via a chain
- That the system connects to your ITSM ontology (Incident, Problem, Change)

---

### Step 2 — GraphRAG: Traverse hidden connections

```cypher
// Find the problems discussed in the paper
MATCH (d:Document {title: "2302.00093"})-[:HAS_CHUNK]->(c:Chunk)
MATCH (c)-[:MENTIONS]->(p:Problem)
WITH DISTINCT p

// What systems are proposed to solve these problems?
MATCH (p)-[:MENTIONS]-(c2:Chunk)-[:MENTIONS]-(sys:System)
WHERE c2 <> c
WITH p, collect(DISTINCT sys.name) AS proposed_systems

// Are these problems also connected to Fairness or Respect?
MATCH (p)-[:MENTIONS]-(c3:Chunk)-[:MENTIONS]-(v)
WHERE v:Fairness OR v:Respect
  OR v:OntologyClass AND v.label IN ['Fairness', 'Respect']

RETURN p.label AS problem,
       proposed_systems,
       collect(DISTINCT v.label) AS ethical_values,
       count(DISTINCT c3) AS ethical_connections
```

**What the graph reveals that RAG alone cannot:**
- Problem X and Problem Y share a proposed System Z (graph sees the overlap)
- Problem X is connected to Fairness through 3 different chunk hops (graph traverses the bridge)
- The paper's technical content has ethical dimensions the author may not have searched for

---

### Step 3 — Web Tools: Verify and ground

```python
from hermes_tools import web_search

# Check if proposed systems were actually deployed
results = web_search("2302.00093 proposed system deployment healthcare 2025 2026")
results += web_search("system_name_from_graph clinical deployment")

# Cross-reference
for r in results:
    print(f"Source: {r['url']}")
    print(f"Claim: {r['title']}")
    print(f"---")
```

**What tools add:**
- The paper may be theoretical → tools prove it stayed theoretical
- Or it may have been deployed under a different name → tools catch the rename
- Ground truth: graphs give structure, tools give current reality

---

## Expected Output (synthesis)

```
Paper 2302.00093 discusses Problems:
  • [Problem A] — proposed solution: [System X]
  • [Problem B] — proposed solution: [System Y]

Graph connections:
  ┌─ Problem A is also linked to Incident types from the ITSM ontology
  ├─ Both problems connect to Fairness through shared Dataset references
  └─ Respect appears in the ontology but not directly linked — this gap is notable

Tool verification:
  ❌ [System X] has no real-world deployment records as of Aug 2026
  ⚠️  [System Y] was cited in 3 follow-up papers but no production implementations
  → The paper remains theoretical, but the graph shows it participates in
    an ethical conversation the authors didn't explicitly frame

Conclusion:
  RAG found the relevant paragraphs.
  The graph showed connections the user didn't know to ask about.
  Tools confirmed no real-world deployment.
  Together, they tell a richer story than any single method.
```

---

## Running This Demo

### One-shot via Hermes

```bash
hermes chat -q "$(cat ~/kb/demo/rag-graph-tools-demo.md | tail -n +2)"
```

### Interactive session

Open Hermes and paste the execution plan steps one at a time,
observing what each layer adds.

---

## Files in This Demo

| File | Purpose |
|------|---------|
| `demo/rag-graph-tools-demo.md` | This script — the hypothesis and execution plan |
| `demo/cypher/step1-vector-rag.cypher` | Vector search query |
| `demo/cypher/step2-graphrag.cypher` | Multi-hop graph traversal |
| `demo/cypher/step3-tools-reference.cypher` | Web tool integration guide |

---

## Appendix: Ontology Reference

Key labels relevant to this demo:

```
Problem — incident, bug, issue, challenge (ITIL-inspired)
System — software, hardware, platform
Dataset — training data, corpus, KB
Fairness — equitable treatment (Values domain)
Respect — dignity and consideration (Values domain)
Document — arXiv paper, article
Chunk — extracted text segment
Fact — extracted triple (confidence=0.75, predicate=RELATED_TO)
```

Current embedding status (pre-fix): Facts at 384-dim, index expects 768-dim.
Run the re-ingest plan before using this demo.