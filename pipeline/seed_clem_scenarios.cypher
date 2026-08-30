// =============================================================================
// neo-mem: Clem Sunter Scenario Lineage Seed
// =============================================================================
// Seeds Clem Sunter's scenario work as a versioned lineage in the knowledge graph.
//
// Model (Option B — graph-native versioning):
//   - Each scenario VERSION is its own :Scenario node, identified by the stable
//     canonical key (scenario_key) + version int.
//   - status = 'active'  | 'superseded' | 'deprecated'
//   - The later node -(SUPERSEDES)-> the earlier node in the same lineage.
//   - name  = display name (may differ per generation)
//   - author = Clem Sunter (Person-[AUTHORED]->Scenario, author_uid only if unclaimed)
//
// Idempotent: MERGE on (scenario_key, version); nodes created here only.
// NOTE: This file creates nodes only. The :SUPERSEDES lineage edges are created by
//       the companion file pipeline/seed_clem_supersedes.cypher (self-contained
//       MATCH-on-(scenario_key,version) statements).
//
// Run via (WSL, against D7 Neo4j):
//   cat pipeline/seed_clem_scenarios.cypher | <cypher-shell to D7:7687>
// or paste into Neo4j Browser.
// =============================================================================

// ═══════════════════════════════════════════════════════════════════════════
// LINEAGE: high_road  (SA positive economic scenario, 1987 → 1996 → present)
// ═══════════════════════════════════════════════════════════════════════════

MERGE (hr1987:Scenario {scenario_key: "high_road", version: 1})
SET hr1987.name = "High Road (1987)",
    hr1987.description = "The High Road / Low Road scenarios from 'The World and South Africa in the 1990s' (Anglo American, 1987): a path of negotiation, peaceful transformation and economic growth for South Africa in the late-apartheid era.",
    hr1987.category = "economic",
    hr1987.status = "superseded",
    hr1987.valid_from = "1987",
    hr1987.valid_until = "1996",
    hr1987.author_uid = "clem-sunter";

MERGE (hr1996:Scenario {scenario_key: "high_road", version: 2})
SET hr1996.name = "The High Road: Where Are We Now? (1996)",
    hr1996.description = "Clem Sunter's 1996 restatement/checkpoint of the High Road scenario after South Africa's democratic transition — assessing how far the country had progressed along the High Road.",
    hr1996.category = "economic",
    hr1996.status = "superseded",
    hr1996.valid_from = "1996",
    hr1996.valid_until = "2023",
    hr1996.author_uid = "clem-sunter";

MERGE (hrLatest:Scenario {scenario_key: "high_road", version: 3})
SET hrLatest.name = "People's Economy (latest)",
    hrLatest.description = "Clem Sunter's latest (2023-2025) SA mainframe scenario: build a new inclusive economy in which everyone participates and shares — the 'economic crossroads' successor to the political crossroads of the early 1990s.",
    hrLatest.category = "economic",
    hrLatest.status = "active",
    hrLatest.valid_from = "2023",
    hrLatest.probability_current = null,
    hrLatest.probability_trend = "stable",
    hrLatest.author_uid = "clem-sunter";

MERGE (hr1987)-[:SUPERSEDES]->(hr1996)
MERGE (hr1996)-[:SUPERSEDES]->(hrLatest);

// ═══════════════════════════════════════════════════════════════════════════
// LINEAGE: low_road  (SA negative economic scenario, 1987 → present)
// ═══════════════════════════════════════════════════════════════════════════

MERGE (lr1987:Scenario {scenario_key: "low_road", version: 1})
SET lr1987.name = "Low Road (1987)",
    lr1987.description = "The Low Road from 'The World and South Africa in the 1990s' (Anglo American, 1987): escalating conflict and instability, economic decline, and failure to achieve a negotiated settlement in apartheid-era South Africa.",
    lr1987.category = "economic",
    lr1987.status = "superseded",
    lr1987.valid_from = "1987",
    lr1987.valid_until = "2023",
    lr1987.author_uid = "clem-sunter";

MERGE (lrLatest:Scenario {scenario_key: "low_road", version: 2})
SET lrLatest.name = "Cautionary Tale (latest)",
    lrLatest.description = "Clem Sunter's latest (2023-2025) SA mainframe scenario: the negative path where South Africa loses its political gains, suffers internal conflict and economic decline, and slides toward a weak/failed state — a cautionary tale of the 'economic crossroads'.",
    lrLatest.category = "economic",
    lrLatest.status = "active",
    lrLatest.valid_from = "2023",
    lrLatest.probability_current = null,
    lrLatest.probability_trend = "stable",
    lrLatest.author_uid = "clem-sunter";

MERGE (lr1987)-[:SUPERSEDES]->(lrLatest);

// ═══════════════════════════════════════════════════════════════════════════
// LINEAGE: wasteland  (SA failure-mode scenario, latest 45/45/10 assessment)
// ═══════════════════════════════════════════════════════════════════════════

MERGE (wasteland:Scenario {scenario_key: "wasteland", version: 1})
SET wasteland.name = "Wasteland / Failed State (latest)",
    wasteland.description = "The tail-risk scenario in Clem Sunter's latest assessment: the Low Road deteriorates into continuous conflict and a failed state — assigned roughly 10% probability in his most recent SA scenario set.",
    wasteland.category = "economic",
    wasteland.status = "active",
    wasteland.valid_from = "2023",
    wasteland.probability_current = 0.1,
    wasteland.probability_trend = "stable",
    wasteland.author_uid = "clem-sunter";

// ═══════════════════════════════════════════════════════════════════════════
// LINEAGES: GLOBAL  (Friendly Planet vs Gilded Cage — latest global set)
// ═══════════════════════════════════════════════════════════════════════════

MERGE (fp:Scenario {scenario_key: "friendly_planet", version: 1})
SET fp.name = "Friendly Planet (latest)",
    fp.description = "Clem Sunter's optimistic global scenario: new leaders act in unison to resolve major conflicts and cut emissions, delivering a more cooperative and sustainable world.",
    fp.category = "global",
    fp.status = "active",
    fp.valid_from = "2023",
    fp.probability_current = null,
    fp.probability_trend = "stable",
    fp.author_uid = "clem-sunter";

MERGE (gc:Scenario {scenario_key: "gilded_cage", version: 1})
SET gc.name = "Gilded Cage (latest)",
    gc.description = "Clem Sunter's pessimistic global scenario: wars drag on, the West retreats into an affluent enclave ('gilded cage'), climate and cooperative action stall, and the world remains divided.",
    gc.category = "global",
    gc.status = "active",
    gc.valid_from = "2023",
    gc.probability_current = null,
    gc.probability_trend = "stable",
    gc.author_uid = "clem-sunter";

// ═══════════════════════════════════════════════════════════════════════════
// AUTHOR ATTRIBUTION: link Clem Sunter Person -> AUTHORED -> each lineage node
// (author_uid only set on the node above if unclaimed; this guarantees the edge)
// ═══════════════════════════════════════════════════════════════════════════

MATCH (p:Person {name: "Clem Sunter"})
MATCH (s:Scenario) WHERE s.author_uid = "clem-sunter"
MERGE (p)-[:AUTHORED]->(s);

// ═══════════════════════════════════════════════════════════════════════════
// NOTE on the original 45/45/10 High/Low/Wasteland probabilities:
//   High Road ~45%, Low Road ~45%, Wasteland ~10%.
//   The three-way 45/45/10 split is an alternative active framing of 'high_road'
//   (positive) and 'low_road' (negative) at once. Rather than duplicate nodes,
//   this seed keeps the two mainframe lineages (positive vs negative) and models
//   Wasteland as its own scenario at 0.10. If the authoritative latest source is
//   the 45/45/10 split itself, set high_road.probability_current=0.45 and
//   low_road.probability_current=0.45.
// =============================================================================
