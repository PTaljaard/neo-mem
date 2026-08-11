# neo-mem Pipeline

Knowledge graph ingestion pipeline for the PhD in RAG+Graph+Tools for CPS.

## Quick Start

```bash
# Ingest today's news (6 articles, 3 claims each)
python3 pipeline/run.py news

# Monitor a commentator's commentary
python3 pipeline/run.py monitor "Frans Cronje" --days 7

# Ingest a PDF paper
python3 pipeline/run.py ingest /srv/kb/pieter/Incoming/paper.pdf

# Extract entities from a document
python3 pipeline/run.py extract-entities dial-kg-2603.20059

# Batch ingest all PDFs
python3 pipeline/run.py batch /srv/kb/pieter/Incoming/
```

## Architecture

```
                      ┌──────────────────────────────────┐
                      │         Neo4j (D7:7687)           │
                      │  (:Document)-[:HAS_CHUNK]->(:Chunk)│
                      │  (:NewsArticle)-[:CONTAINS_CLAIM] │
                      │    ->(:Claim)                     │
                      │  (:Person)-[:MENTIONS]            │
                      │    ->(:NewsArticle)               │
                      │  (:Fact)-[:HAS_SUBJECT]->(:Entity)│
                      │  (:Fact)-[:HAS_OBJECT]->(:Entity) │
                      └──────────────────────────────────┘
                               ▲
          ┌────────────────────┼────────────────────┐
          │                    │                    │
     ┌────┴────┐         ┌────┴────┐          ┌────┴────┐
     │  News   │         │  Person │          │  PDF    │
     │ Ingester│         │ Monitor │          │ Ingester│
     │ (cron)  │         │ (cron)  │          │ (manual)│
     └─────────┘         └─────────┘          └─────────┘
```

## Pipeline Scripts

| Script | Purpose | Run via |
|--------|---------|---------|
| `run.py` | Single entry point for all operations | `python3 run.py <command>` |
| `pipeline_config.py` | Shared config (Neo4j, Ollama, paths) | Imported by all scripts |
| `news_ingester.py` | RSS news → NewsArticle + Claim nodes | `run.py news` or cron 06:35 |
| `person_monitor.py` | Search commentator → Person + NewsArticle + Claim | `run.py monitor "Name"` |
| `ingest_document.py` | PDF → Document/Chunk with 768-dim embeddings | `run.py ingest path.pdf` |
| `extract_entities.py` | LLM entity/fact extraction from chunks | `run.py extract-entities doc-uid` |
| `batch_ingest.py` | Batch PDF processing | `run.py batch /dir/` |
| `migrate_claims_to_nodes.py` | One-time migration: arrays → Claim nodes | `run.py migrate-claims` |
| `add_confidence_schema.py` | One-time: add confidence dimensions to Facts | `run.py add-confidence-schema` |
| `verify_claims.py` | Verify Claim node structure in Neo4j | `run.py verify-claims` |
| `verify_commentators.py` | Verify commentator graph | `run.py verify-commentators` |

## Configuration

All config is in `pipeline_config.py`, read from environment variables:

```bash
# Defaults point to the home setup:
NEO4J_URI=bolt://192.168.0.114:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=Erna#26neo4j
OLLAMA_BASE_URL=http://192.168.0.200:11434/v1
EMBEDDING_MODEL=nomic-embed-text
CHAT_MODEL=gemma4:e4b-it-qat
KB_ROOT=/srv/kb/pieter
```

Override any setting for travel or testing:
```bash
# When traveling (R9 only, no D7):
NEO4J_URI=bolt://localhost:7687 python3 run.py news --max-articles 3
```

## Ontology

The ontology is defined in `examples/ontology/seed.cypher` and loaded into Neo4j as `:OntologyClass` nodes.

### Class hierarchy
```
Root → Upper → Claim, NewsArticle, Document, Chunk, Fact,
               Person, Organization, System, Event, Dataset, ...
```

### Key node types
- `(:NewsArticle {uid, title, body, embedding, source, category, claim_count})`
- `(:Claim {uid, text, confidence, extracted_at, source_uid})`
- `(:Person {name, role})` — set `role: 'Commentator'` for monitored persons
- `(:Fact {uid, text, embedding, confidence_evidence_quality, confidence_strategic, ...})`
- `(:Document {uid, title, source, arxiv_id, ...})`
- `(:Chunk {uid, text, embedding[768], chunk_index, doc_id})`
- `(:Entity {name, type: PAPER|METHOD|CONCEPT|...})`

### Key relationships
- `(:NewsArticle)-[:CONTAINS_CLAIM]->(:Claim)`
- `(:NewsArticle)-[:MENTIONS]->(:Person)`
- `(:Document)-[:HAS_CHUNK]->(:Chunk)`
- `(:Chunk)-[:MENTIONS]->(:Entity)`
- `(:Chunk)-[:CONTAINS_FACT]->(:Fact)`
- `(:Fact)-[:HAS_SUBJECT]->(:Entity)`
- `(:Fact)-[:HAS_OBJECT]->(:Entity)`
- `(:Entity)-[:RELATED_TO]-(:Entity)`

## Cron Jobs (R9)

| Job | Schedule | What |
|-----|----------|------|
| News ingester | 06:35 daily | Fetches 6 articles, stores in Neo4j with claims |
| Monthly scenario review | 1st of month 09:00 | Reviews indicator movement across scenarios |
| Commentator monitors | Various | Dawie, Azar, Frans — currently Telegram only |

To add a commentator to the cron pipeline:
```bash
# Create a cron job that runs the person monitor:
cronjob action=create schedule="0 8 * * *" \
  prompt="Run python3 pipeline/run.py monitor 'Frans Cronje' --days 7" \
  deliver=telegram:8730141040
```

## Travel Mode (R9 goes, no D7)

When R9 travels, the pipeline still works:
- **Ollama**: Local gemma4 + nomic-embed-text (no change needed)
- **Neo4j**: Point to local instance or use Tailscale → D7
- **OpenRouter**: DeepSeek for PhD work (needs internet)
- **KB**: Sync before departure: `rsync -avz d7-gbe:/srv/kb/pieter/research/ ~/phd-research/`

```bash
# Travel config:
export NEO4J_URI=bolt://100.85.10.62:7687  # D7 via Tailscale
# Or use local Neo4j:
export NEO4J_URI=bolt://localhost:7687
```

## Related Repositories

- **neo-mem** (this repo): Pipeline + Hermes memory plugin
- **AI-Agent-CPS-work**: Original pipeline (WSL-based, older)
- **Hermes Agent**: The AI agent that drives this (https://github.com/NousResearch/hermes-agent)

## Future Work (Kanban)

Tasks tracked on the `phd-pipeline` kanban board:
```bash
hermes kanban boards switch phd-pipeline
hermes kanban list
```

Priority order:
1. Knowledge capture workflow (save Hermes research to KB)
2. Phase 1: Governance Adjudication (validation before committing)
3. Scenario + Indicator ontology (strategic intelligence)
4. Phase 2: Meta-Knowledge Base (ontology versioning)
5. Event extraction (n-ary relations)
6. AutoKG hybrid search (graph + vector)
7. Full DIAL-KG cycle