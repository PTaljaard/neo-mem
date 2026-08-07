# FAQ

## Embeddings & vector search

### How does semantic recall actually work?

1. Your question is sent to the embedding model (`nomic-embed-text` by default,
   running locally via Ollama).
2. The model returns a 768-dimensional vector — a mathematical representation
   of the *meaning* of your question.
3. That vector is sent to Neo4j's `db.index.vector.queryNodes`, which searches
   the `fact_embedding_index` (HNSW algorithm).
4. Neo4j returns the facts whose stored vectors are closest to your question's
   vector (cosine similarity).
5. The top matches are injected into the agent's context before it answers.

### Is nomic-embed-text an alternative to Neo4j?

**No.** They do different jobs:

- `nomic-embed-text` **creates** vectors (text → numbers).
- Neo4j **stores and searches** vectors (numbers → similar facts).

You need both. The plugin uses a local embedding model (free, private) and
Neo4j's vector index for storage/search. It's a complementary stack, not a
choice between two options.

### Why Neo4j for vector storage instead of Qdrant / pgvector?

For agent memory (thousands of facts), Neo4j is the better fit:

- **Graph traversal** — you can walk relationships (Fact → Topic → Fact),
  which a pure vector DB cannot do.
- **Atomic writes** — fact + embedding are written in one transaction.
- **One system** — no need to keep a vector DB and a graph DB in sync.
- **Same algorithm** — Neo4j 5.11+ uses HNSW, the same ANN algorithm as Qdrant
  and Pinecone, so search speed/quality is comparable at this scale.

Qdrant shines at millions of vectors and very high write throughput — that's
why the document pipeline uses it for chunks, while the memory plugin uses
Neo4j. See `ARCHITECTURE.md`.

### What if I change the embedding model?

The Neo4j vector index has a **fixed dimension** set at creation time (768 for
`nomic-embed-text`). If you switch to a model that emits a different dimension
(e.g., OpenAI's 1536), the index must be dropped and recreated:

```cypher
DROP INDEX fact_embedding_index IF EXISTS;
```

Set `EMBEDDING_DIMENSION` to the new value, then restart the plugin. The
plugin logs a clear warning on startup if the model's output dimension doesn't
match the configured/indexed dimension.

### Which embedding model should I use?

| Model | Provider | Dim | Cost | Notes |
|---|---|---|---|---|
| `nomic-embed-text` | Ollama (local) | 768 | Free | Default. Private, fast on GPU |
| `text-embedding-3-small` | OpenAI | 1536 | $0.02/1M tokens | Better quality, costs money |
| `text-embedding-3-large` | OpenAI | 3072 | $0.13/1M tokens | Best quality, most expensive |

For most use cases `nomic-embed-text` is plenty. Upgrade only if recall quality
is measurably insufficient.

## Configuration

### What environment variables do I need?

See `.env.example`. Minimum required: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASS`.
Everything else has sensible defaults (Ollama local embeddings).

### Do I need an OpenAI API key?

**No** — the default is local Ollama embeddings, no key needed. An OpenAI /
OpenRouter key is only required if you set `EMBEDDING_PROVIDER=openai`.

### Does this work with Claude Code / Codex?

Yes. The MCP server (`mcp/README.md`) exposes the Neo4j graph to any MCP
client. The plugin is Hermes-specific; the MCP server is agent-agnostic.

## Operations

### Where are my credentials stored?

In your `.env` file or your agent's config (e.g., Hermes `config.yaml`).
Neither is committed to the repo — `.gitignore` excludes `.env`, and
`.env.example` contains placeholders only.

### What happens if Neo4j is down?

The plugin logs a warning and skips memory writes/reads. Your agent keeps
working — it just has no memory recall until Neo4j is back.

### How do I wipe all memories?

```cypher
MATCH (f:Fact) DETACH DELETE f;
```

This clears stored facts (the vector index remains, ready for new writes).

## Project

### Why the name "neo4j-graphrag-memory" if this is "neo-mem"?

`neo4j-graphrag-memory` is the provider identifier that the Hermes config
references (`memory.provider`). Keeping it ensures backward compatibility —
anyone already using the plugin doesn't have to change their config. The
project itself is branded **neo-mem**.

### Is this production-ready?

It is used in production daily by the author for a PhD research project on
Agentic Complex Problem Solving. It stores real conversation memory in a live
Neo4j graph. That said, it's a small focused project — review the code and
adapt to your needs.

### Why are PR reviews slow?

Single maintainer, research-driven schedule. See `CONTRIBUTING.md` for
expectations and guidelines.