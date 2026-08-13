# Critic-Defender-HITL Review Pipeline

## Architecture

### Problem
- 680 events extracted by gemma4, quality unknown
- 700+ more coming from DeepSeek (10% test)
- Many edges may be low-value noise
- Need systematic critique without losing insights

### Solution: 4-Stage Adversarial Review

```
Stage 1: COLLECT (cron → Neo4j)
Stage 2: CRITIQUE (critic model → CritiqueReport)
Stage 3: DEFEND (original model → DefenseReport)
Stage 4: HITL (you → final verdict)
```

### Neo4j Schema

```cypher
(:Event)-[:HAS_CRITIQUE]->(:CritiqueReport {
    uid, created_at, critic_model,
    credibility_score,       // 0-1: how credible is this event
    source_quality,          // 0-1: quality of source chunk
    contradiction_count,     // how many facts contradict
    missing_context,         // list of missing entities/context
    confidence_attenuation,  // 0-1: reduce confidence by this factor
    critique_text            // natural language critique
})

(:CritiqueReport)<-[:HAS_DEFENSE]-(:DefenseReport {
    uid, created_at, defender_model,
    accepts_critique,        // boolean
    amended_event,           // revised event text if changed
    justification,           // why original stands or was changed
    new_confidence,          // revised confidence after defense
    amended_description      // revised description text
})

(:Event)<-[:HITL_REVIEWED]-(:HITLDecision {
    uid, reviewed_at, reviewer,
    status,                  // KEEP / REVISE / REJECT
    final_description,       // HITL-approved text
    notes,                    // reviewer notes
    knowledge_confidence     // HIGH / MEDIUM / LOW
})
```

### Stage Details

**Stage 1 — COLLECT (cron, daily/weekly)**
- Existing pipeline: news_ingester.py → Neo4j
- New: papers/hermes-research cron → Neo4j
- New: health-research cron → Neo4j

**Stage 2 — CRITIQUE (runs after ingest)**
- Critic model reads: event_text + source_chunk + existing_kg_context
- Critic model outputs: CritiqueReport
- Critic should be DIFFERENT model from extractor for adversarial value
  - If gemma4 extracted → DeepSeek critic
  - If DeepSeek extracted → gemma4 critic (free)

**Stage 3 — DEFEND (runs after critique)**
- Defender model reads: original_event + CritiqueReport
- Defender can: accept critique (amend), reject critique (justify), or partial amend
- Defender outputs: DefenseReport

**Stage 4 — HITL (weekend, batch)**
- You review: [Event + Critique + Defense] per item
- Decision: KEEP / REVISE / REJECT
- Tagged with knowledge_confidence for downstream use

### Guardrails

```
1. Only critique events where:
   - credibility_score < 0.8 (already suspicious)
   - OR contradiction_count > 0 (conflicts with KG)
   - OR source_quality < 0.6 (noisy source)

2. Skip high-confidence consensus events
   (both models agree = KEEP without HITL)

3. Batch HITL: review 5-10 at a time, not 50
```

### Existing Edge Cleanup Strategy

Priority tiers for the current 680 gemma4 events:

| Tier | Criteria | Count (est.) | Action |
|------|----------|-------------|--------|
| **T1: Consensus** | Both gemma4 + DeepSeek find same event | ~200 | Auto-KEEP |
| **T2: DeepSeek-only** | DeepSeek finds event gemma4 missed | ~150 | Auto-ADD (DeepSeek higher quality) |
| **T3: Gemma4-only** | gemma4 found, DeepSeek did not | ~330 | Flag for critique |
| **T4: Low-value** | Single-word events, procedural noise | ~100 | Auto-REJECT |

The 10% DeepSeek test will give us actual numbers for this breakdown.

### Implementation Plan

1. Wait for 10% DeepSeek test to complete
2. Run `compare_models.py` to get consensus/gap stats
3. Build `critique_pipeline.py`:
   - Takes an Event node → sends to critic LLM → creates CritiqueReport
   - Takes CritiqueReport → sends to defender LLM → creates DefenseReport
   - Batch mode for efficiency
4. Build `hitl_review.py`:
   - Lists events pending HITL with their Critique+Defense
   - `--approve`, `--reject`, `--revise` actions
   - Updates knowledge_confidence on Event node
5. Integrate into `run.py` as `critique` and `hitl` subcommands
6. Set up weekly cron: Saturday critique+defense → Sunday HITL review