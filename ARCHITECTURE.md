# Architecture

## Why Neo4j for memory (not Qdrant)

The project stores **conversation memory facts** — typically thousands of nodes,
sometimes tens of thousands, but rarely millions.  Neo4j's native vector index
(available since 5.11) is well-suited to this scale and offers unique benefits
over a pure vector DB:

| Dimension | Neo4j (chosen) | Qdrant / pgvector |
|---|---|---|
| Scale | Thousands — fits easily | Millions — overkill here |
| Graph traversal | ✅ Fact → Topic → Fact hops | ❌ No relationships |
| Atomic writes | ✅ Fact + embedding in one tx | ❌ Requires sync |
| Standalone | ✅ One DB | ❌ Need Neo4j anyway for data |
| Vector search | ✅ ANN via `db.index.vector.queryNodes` | ✅ Faster at large scale |
| Latency at 10k nodes | ~5-10 ms | ~2-5 ms (negligible difference) |

**Decision:** Neo4j is the right home for agent memory.  Qdrant remains in the
stack for document chunk ingestion (the pipeline), where scale and throughput
matter more than graph traversal.

## Embedding providers

The plugin supports any **OpenAI-compatible** embedding API.  This includes:

- **Ollama** (default) — `nomic-embed-text` (768 dim), free, local GPU
- **OpenAI** — `text-embedding-3-small` (1536 dim) or `text-embedding-3-large` (3072 dim)
- **OpenRouter** — any embedding model via OpenAI-compatible endpoint

### ⚠️ Dimension mismatch risk

The Neo4j vector index is created with a fixed `vector.dimension` — currently
**768** (nomic-embed-text).  If you switch to a model that produces a different
dimension (e.g., OpenAI's 1536), the vector index must be **dropped and
recreated**:

```cypher
DROP INDEX fact_embedding_index IF EXISTS;
```

Set `EMBEDDING_DIMENSION` to the new dimension before restarting the plugin.
The plugin logs a warning on startup if the model's output dimension doesn't
match the configured dimension.

## Vector search

The plugin uses Neo4j's `db.index.vector.queryNodes` for Approximate Nearest
Neighbor (ANN) search — the same algorithm used by Qdrant and Pinecone, backed
by the Lucene / HNSW index in Neo4j 5.11+.  This is faster and more accurate
than the manual cosine-similarity scan that the original plugin used.

## Provider identifier

The `name` property returns `"neo4j-graphrag-memory"` for backward compatibility
with the original Hermes plugin name.  The project is also known as `neo-mem`
— either identifier works in configuration.

## Dependencies

Minimal by design:

| Package | Why |
|---|---|
| `neo4j` | Bolt driver for graph database |
| `openai` | OpenAI-compatible embedding API (works with Ollama too) |
| `numpy` | Vector operations and type handling |

Notably absent: `sentence-transformers` (2+ GB), `langchain` (heavy framework),
`qdrant-client` (separate tool for the pipeline, not the memory plugin).