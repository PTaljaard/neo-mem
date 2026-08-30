// =============================================================================
// neo-mem: Clem Sunter Scenario Lineage — SUPERSEDES edges (self-contained)
// =============================================================================
// Creates the :SUPERSEDES lineage edges between scenario versions.
// Each statement matches both endpoint nodes by (scenario_key, version), so each
// is fully self-contained (no reliance on variables from other statements).
// Idempotent: MERGE on endpoint match.
// =============================================================================

// high_road: 1987 (v1) --> 1996 (v2) --> People's Economy (v3, active)
MATCH (a:Scenario {scenario_key: "high_road", version: 1})
MATCH (b:Scenario {scenario_key: "high_road", version: 2})
MERGE (a)-[:SUPERSEDES]->(b);

MATCH (a:Scenario {scenario_key: "high_road", version: 2})
MATCH (b:Scenario {scenario_key: "high_road", version: 3})
MERGE (a)-[:SUPERSEDES]->(b);

// low_road: 1987 (v1) --> Cautionary Tale (v2, active)
MATCH (a:Scenario {scenario_key: "low_road", version: 1})
MATCH (b:Scenario {scenario_key: "low_road", version: 2})
MERGE (a)-[:SUPERSEDES]->(b);
