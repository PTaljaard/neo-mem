// =============================================================================
// neo-mem: Example Ontology Seed
// =============================================================================
//
// This file populates a Neo4j instance with a minimal common ontology that
// demonstrates the class hierarchy, property definitions, multi-tenancy model,
// and memory structure used by the neo-mem plugin.
//
// Load it:
//   cat examples/ontology/seed.cypher | docker compose exec -T neo4j cypher-shell -u neo4j -p "${NEO4J_PASSWORD}"
//
// Or via the Neo4j Browser: paste and run the whole file.
// =============================================================================

// ── 1. META LAYER (foundation of the type system) ───────────────────────────

MERGE (root:OntologyClass {
  class_id:   "Root",
  label:      "Root",
  description: "Root ontology class from which all others inherit"
})
SET root.updated_at = datetime(),
    root.extends    = "None",
    root.properties = ["class_id", "label", "description", "extends", "updated_at"];

// ── 2. UPPER LAYER (bridge classes — shared by all domains) ─────────────────

MERGE (:OntologyClass {
  class_id:   "Upper",
  label:      "Upper",
  description: "Bridge ontology for classes shared across all domains — people, organisations, systems, policies, data, and documents",
  extends:    "Root"
})
SET updated_at = datetime(),
    properties = [];

// ── 2a. Core agent-memory nodes ─────────────────────────────────────────────

MERGE (:OntologyClass {
  class_id:   "Fact",
  label:      "Fact",
  description: "A memory fact — the core unit of agent memory. Each fact stores a conversational claim with a vector embedding for semantic recall",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "content", "embedding", "created_at", "tenant", "source_session"];

MERGE (:OntologyClass {
  class_id:   "Chunk",
  label:      "Chunk",
  description: "A text chunk extracted from a document during ingestion, with a vector embedding for semantic search",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "content", "embedding", "position", "document_id", "tenant"];

MERGE (:OntologyClass {
  class_id:   "Document",
  label:      "Document",
  description: "A formal document, file, or knowledge artifact that contains textual content",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "title", "source_uri", "content_type", "ingested_at", "tenant"];

// ── 2c. News & Claims ───────────────────────────────────────────────────

MERGE (:OntologyClass {
  class_id:   "Claim",
  label:      "Claim",
  description: "A factual assertion extracted from a news article. The atomic unit of strategic intelligence.",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "text", "confidence", "extracted_at", "source_uid"];

MERGE (:OntologyClass {
  class_id:   "NewsArticle",
  label:      "NewsArticle",
  description: "A news article ingested from an RSS feed with full text, metadata, and extracted claims.",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "title", "summary", "body", "url", "source", "category",
                  "fetched_at", "published_at", "embedding"];

// ── 2d. Shared domain entities ──────────────────────────────────────────────

MERGE (:OntologyClass {
  class_id:   "Person",
  label:      "Person",
  description: "A human individual with agency — used for users, subjects, authors, and stakeholders across all domains",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "email", "role", "tenant", "shared"];

MERGE (:OntologyClass {
  class_id:   "Organization",
  label:      "Organization",
  description: "A legal entity, institution, company, or collective group with an identity and structure",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "industry", "tenant", "shared"];

MERGE (:OntologyClass {
  class_id:   "System",
  label:      "System",
  description: "A software or hardware system — applications, platforms, infrastructure components, or services",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "version", "system_type", "tenant"];

MERGE (:OntologyClass {
  class_id:   "Policy",
  label:      "Policy",
  description: "A rule, standard, or guideline that governs behaviour, decisions, or processes within an organisation",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "title", "jurisdiction", "effective_date", "tenant", "shared"];

MERGE (:OntologyClass {
  class_id:   "Dataset",
  label:      "Dataset",
  description: "A structured collection of data — tables, knowledge bases, corpora, or exports",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "schema", "record_count", "tenant", "shared"];

MERGE (:OntologyClass {
  class_id:   "AI_Agent",
  label:      "AI_Agent",
  description: "An artificial intelligence system — LLM, agent framework, or autonomous assistant — treated as a participant in decisions and conversations",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "provider", "model", "tenant"];

MERGE (:OntologyClass {
  class_id:   "Event",
  label:      "Event",
  description: "A notable occurrence — incidents, meetings, transactions, alerts, or any timestamped happening",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "timestamp", "event_type", "severity", "tenant"];

MERGE (:OntologyClass {
  class_id:   "Role",
  label:      "Role",
  description: "A defined position, function, or responsibility within an organisation or system",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "permissions", "tenant", "shared"];

MERGE (:OntologyClass {
  class_id:   "Task",
  label:      "Task",
  description: "A unit of work — actionable items, assignments, or checklist entries that can be tracked to completion",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "summary", "status", "assignee", "due_date", "tenant"];

// ── 2c. Common reference data ───────────────────────────────────────────────

MERGE (:OntologyClass {
  class_id:   "Standard",
  label:      "Standard",
  description: "A codified benchmark, framework, or specification (ISO, GDPR, HIPAA, SOC 2, etc.)",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "name", "version", "authority", "shared"];

MERGE (:OntologyClass {
  class_id:   "Contract",
  label:      "Contract",
  description: "A formal agreement between two or more parties, defining rights, obligations, and terms",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = ["uid", "title", "parties", "effective_date", "expiry_date", "tenant"];

// ── 3. DOMAIN CONTAINERS (for downstream specialisation) ────────────────────

MERGE (:OntologyClass {
  class_id:   "Domain",
  label:      "Domain",
  description: "Container ontology for domain-specific class hierarchies (e.g., Medical, Financial, DevOps)",
  extends:    "Upper"
})
SET updated_at = datetime(),
    properties = [];

// Example domain (uncomment and expand for your use case):
//
// MERGE (:OntologyClass {
//   class_id: "Medical",
//   label:    "Medical",
//   description: "Ontology for clinical care, patient records, and medical governance",
//   extends: "Domain"
// }) SET updated_at = datetime(), properties = [];
//
// MERGE (:OntologyClass {
//   class_id: "Patient",
//   label:    "Patient",
//   description: "An individual receiving medical care",
//   extends: "Medical"
// }) SET updated_at = datetime(),
//     properties = ["uid", "mrn", "name", "dob", "tenant"];

// ── 3b. Meta-Knowledge Base (MKB) — ontology versioning & schema evolution ──
//
// These classes track changes to the ontology itself over time.
// Phase 2 of the ontology governance architecture.
//
// OntologyVersion: a point-in-time snapshot of all OntologyClass + Property nodes.
// SchemaChange: a single evolution event (add/remove/modify class or property).
// SchemaProposal: a set of proposed changes, reviewed via HITL workflow.
// RelationshipType: metadata node tracking relationship types used in the graph.

MERGE (:OntologyClass {
  class_id:   "OntologyVersion",
  label:      "OntologyVersion",
  description: "A versioned snapshot of the ontology — captures all OntologyClass and Property definitions at a point in time. Enables rollback, diffing, and audit trail.",
  extends:    "Root"
})
SET updated_at = datetime(),
    properties = ["version", "created_at", "description", "class_count",
                  "property_count", "snapshot", "source"];

MERGE (:OntologyClass {
  class_id:   "SchemaChange",
  label:      "SchemaChange",
  description: "A single ontology evolution event — the atomic unit of schema change tracking. Records what changed, when, why, and by whom.",
  extends:    "Root"
})
SET updated_at = datetime(),
    properties = ["uid", "timestamp", "change_type", "target", "before",
                  "after", "justification", "proposal_uid", "source", "status"];

MERGE (:OntologyClass {
  class_id:   "SchemaProposal",
  label:      "SchemaProposal",
  description: "A proposed set of schema changes, reviewed by a human (HITL) before being applied. May be auto-generated from extraction pattern analysis or created manually.",
  extends:    "Root"
})
SET updated_at = datetime(),
    properties = ["uid", "created_at", "status", "proposed_changes",
                  "evidence", "source", "summary",
                  "reviewed_by", "reviewed_at", "review_notes"];

MERGE (:OntologyClass {
  class_id:   "RelationshipType",
  label:      "RelationshipType",
  description: "A metadata node tracking a relationship type used in the graph — its description, when it was first observed, and whether it has an ontology-level definition.",
  extends:    "Root"
})
SET updated_at = datetime(),
    properties = ["type", "description", "created_at", "deprecated"];

// ── 4. PROPERTY DEFINITIONS ─────────────────────────────────────────────────

// Universal properties
MERGE (:Property {key: "uid",         description: "Unique identifier across the graph",          datatype: "string"});
MERGE (:Property {key: "label",       description: "Display label / short name for the node",     datatype: "string"});
MERGE (:Property {key: "name",        description: "Human-readable name",                         datatype: "string"});
MERGE (:Property {key: "description", description: "Human-readable description or summary",       datatype: "string"});
MERGE (:Property {key: "created_at",  description: "Timestamp of node creation",                  datatype: "datetime"});
MERGE (:Property {key: "updated_at",  description: "Timestamp of last modification",              datatype: "datetime"});

// Multi-tenancy
MERGE (:Property {key: "tenant",  description: "Tenant identifier for multi-tenancy isolation",   datatype: "string"});
MERGE (:Property {key: "shared",  description: "Boolean flag — true if this node is shared across all tenants", datatype: "boolean"});

// Fact / memory
MERGE (:Property {key: "content",       description: "Text content of the fact or chunk",         datatype: "string"});
MERGE (:Property {key: "embedding",     description: "Vector embedding for semantic search",       datatype: "list<float>"});
MERGE (:Property {key: "source_session", description: "Session ID that created this fact",        datatype: "string"});

// Documents
MERGE (:Property {key: "title",         description: "Document or article title",                 datatype: "string"});
MERGE (:Property {key: "source_uri",    description: "URI / file path / URL of the source",       datatype: "string"});
MERGE (:Property {key: "content_type",  description: "MIME type or content format",               datatype: "string"});
MERGE (:Property {key: "ingested_at",   description: "When the document was ingested",            datatype: "datetime"});

// People & organisations
MERGE (:Property {key: "email",         description: "Email address",                              datatype: "string"});
MERGE (:Property {key: "industry",      description: "Industry sector or vertical",                datatype: "string"});

// Events & tasks
MERGE (:Property {key: "timestamp",     description: "ISO-8601 timestamp of occurrence",           datatype: "datetime"});
MERGE (:Property {key: "event_type",    description: "Categorisation of the event type",           datatype: "string"});
MERGE (:Property {key: "severity",      description: "Severity level (critical, high, medium, low)", datatype: "string"});
MERGE (:Property {key: "status",        description: "Current status (open, in_progress, resolved, closed)", datatype: "string"});
MERGE (:Property {key: "due_date",      description: "Deadline or due date",                       datatype: "datetime"});
MERGE (:Property {key: "assignee",      description: "UID or name of the responsible party",      datatype: "string"});

// Meta-Knowledge Base (MKB) — schema evolution
MERGE (:Property {key: "version",         description: "Semantic version string (e.g. v1.0, v1.1)", datatype: "string"});
MERGE (:Property {key: "snapshot",        description: "JSON snapshot of all ontology classes + properties at a version point", datatype: "string"});
MERGE (:Property {key: "source",          description: "Origin of this node (manual, pipeline, auto-extract, auto)", datatype: "string"});
MERGE (:Property {key: "change_type",     description: "Type of schema change (ADD_CLASS, REMOVE_CLASS, MODIFY_CLASS, ADD_PROPERTY, ...)", datatype: "string"});
MERGE (:Property {key: "target",          description: "The class_id or property key this change affects", datatype: "string"});
MERGE (:Property {key: "before",          description: "JSON representation of the state before the change", datatype: "string"});
MERGE (:Property {key: "after",           description: "JSON representation of the state after the change", datatype: "string"});
MERGE (:Property {key: "justification",   description: "Why this schema change was made",            datatype: "string"});
MERGE (:Property {key: "proposal_uid",    description: "Link to the SchemaProposal that triggered this change", datatype: "string"});
MERGE (:Property {key: "proposed_changes", description: "JSON array of change objects proposed in a SchemaProposal", datatype: "string"});
MERGE (:Property {key: "evidence",        description: "JSON object with extraction pattern evidence supporting a proposal", datatype: "string"});
MERGE (:Property {key: "summary",         description: "Short human-readable summary of the proposal's changes", datatype: "string"});
MERGE (:Property {key: "reviewed_by",     description: "Name or identifier of the HITL reviewer",   datatype: "string"});
MERGE (:Property {key: "reviewed_at",     description: "Timestamp when the HITL review occurred",   datatype: "datetime"});
MERGE (:Property {key: "review_notes",    description: "Free-text notes from the HITL reviewer",    datatype: "string"});
MERGE (:Property {key: "class_count",     description: "Number of OntologyClass nodes in a version snapshot", datatype: "integer"});
MERGE (:Property {key: "property_count",  description: "Number of Property nodes in a version snapshot", datatype: "integer"});
MERGE (:Property {key: "deprecated",      description: "Boolean flag — true if this node is deprecated", datatype: "boolean"});
MERGE (:Property {key: "deprecated_at",   description: "Timestamp when this node was deprecated",   datatype: "datetime"});

// ── 5. RELATIONSHIP TYPES (schema hints for the graph model) ─────────────────
//
// The following relationships are created as semantic links between example nodes.
// They are not stored as nodes themselves — they are actual Neo4j relationships.

// Meta-Knowledge Base (MKB) relationships
// (:SchemaProposal)-[:TRIGGERED]->(:SchemaChange) — which proposal caused this change
// (:SchemaChange)-[:UPDATED_CLASS]->(:OntologyClass) — which class was changed
// (:SchemaChange)-[:UPDATED_PROPERTY]->(:Property) — which property was changed

// ── 6. EXAMPLE DATA ─────────────────────────────────────────────────────────

// Person: Researcher (example tenant A)
MERGE (p_a:Person:Upper {
  uid:    "person-researcher",
  name:   "Alex Researcher",
  email:  "alex@example.com",
  role:   "researcher",
  tenant: "tenant-a"
})
SET p_a.created_at = datetime(), p_a.updated_at = datetime();

// Person: Professional (example tenant B)
MERGE (p_b:Person:Upper {
  uid:    "person-professional",
  name:   "Bob Professional",
  email:  "bob@example.com",
  role:   "professional",
  tenant: "tenant-b"
})
SET p_b.created_at = datetime(), p_b.updated_at = datetime();

// Organization (shared)
MERGE (org:Organization:Upper {
  uid:    "org-ourapp",
  name:   "Our Research Organisation",
  shared: true
})
SET org.created_at = datetime(), org.updated_at = datetime();

// Link people to their organisation
MATCH (p_a:Person {uid: "person-researcher"})
MATCH (o:Organization {uid: "org-ourapp"})
MERGE (p_a)-[:WORKS_FOR]->(o);

MATCH (p_b:Person {uid: "person-professional"})
MATCH (o:Organization {uid: "org-ourapp"})
MERGE (p_b)-[:WORKS_FOR]->(o);

// AI Agents (one per tenant, shared agent definition)
MERGE (a1:AI_Agent:Upper {
  uid:      "agent-hermes-a",
  name:     "Hermes Agent",
  provider: "Nous Research",
  model:    "deepseek/deepseek-v4-flash",
  tenant:   "tenant-a"
})
SET a1.created_at = datetime();

MERGE (a2:AI_Agent:Upper {
  uid:      "agent-hermes-b",
  name:     "Hermes Agent",
  provider: "Nous Research",
  model:    "deepseek/deepseek-v4-flash",
  tenant:   "tenant-b"
})
SET a2.created_at = datetime();

// A sample Fact (memory)
MERGE (f:Fact:Upper {
  uid:            "fact-0001",
  content:        "Neo4j is the primary graph database, running on D7 at bolt://192.168.0.114:7687",
  embedding:      [],    // ← real embedding is set by the plugin
  created_at:     datetime(),
  source_session: "seed",
  tenant:         "tenant-a"
})
SET f.updated_at = datetime();

// Link fact to its subjects
MERGE (s:System:Upper {uid: "sys-neo4j-d7", name: "Neo4j D7", system_type: "database", tenant: "tenant-a", shared: true})
SET s.created_at = datetime();

MATCH (f:Fact {uid: "fact-0001"})
MATCH (s:System {uid: "sys-neo4j-d7"})
MERGE (f)-[:HAS_SUBJECT]->(s);

// ── 7. PRINT SUMMARY ────────────────────────────────────────────────────────

RETURN "Seed complete"
  AS status,
  count(DISTINCT {label: "OntologyClass"}) + count { MATCH (:OntologyClass) } AS ontology_classes,
  count { MATCH (:Property) } AS properties,
  count { MATCH (:Person) }   AS people,
  count { MATCH (:Fact) }     AS facts;