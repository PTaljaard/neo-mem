# MCP server — Neo4j Cypher

A **Model Context Protocol** server that gives your agent Cypher query
capabilities against Neo4j.  Works with any MCP client (Hermes, Claude Code,
Codex, etc.).

## Install

```bash
pip install mcp-neo4j-cypher
```

## Configure

### Hermes (config.yaml)

```yaml
mcp_servers:
  neo4j:
    command: python
    args: ["-m", "mcp_neo4j_cypher"]
    env:
      NEO4J_URI: bolt://localhost:7687
      NEO4J_USERNAME: neo4j
      NEO4J_PASSWORD: change-me
      NEO4J_DATABASE: neo4j
    enabled: true
```

### Claude Code / Codex

```json
{
  "mcpServers": {
    "neo4j": {
      "command": "python",
      "args": ["-m", "mcp_neo4j_cypher"],
      "env": {
        "NEO4J_URI": "bolt://localhost:7687",
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "change-me",
        "NEO4J_DATABASE": "neo4j"
      }
    }
  }
}
```

## Provided tools

| Tool | Description |
|---|---|
| `neo4j_query` | Run read-only Cypher queries |
| `neo4j_schema` | Inspect the database schema (labels, relationships, properties) |