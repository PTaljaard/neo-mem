"""Neo4j-backed GraphRAG memory provider for AI agents.

A persistent memory backend that stores conversational facts as nodes in Neo4j
with vector embeddings, enabling semantic similarity search and graph traversal.

Configuration (via environment variables):
    NEO4J_URI               bolt://localhost:7687
    NEO4J_USER              neo4j
    NEO4J_PASSWORD          (your password)
    NEO4J_DATABASE          neo4j

    EMBEDDING_PROVIDER      ollama | openai   (default: ollama)
    EMBEDDING_MODEL         nomic-embed-text   (default; ignored by openai)
    EMBEDDING_BASE_URL      http://localhost:11434/v1
    EMBEDDING_API_KEY       ollama
    EMBEDDING_DIMENSION     768

    NEO4J_EMBEDDING_DIMENSION   768  (Neo4j vector index dimension)
    NEO4J_SIMILARITY_THRESHOLD  0.75

For Ollama (default / user's setup):
    EMBEDDING_PROVIDER=ollama
    EMBEDDING_MODEL=nomic-embed-text
    EMBEDDING_BASE_URL=http://localhost:11434/v1
    EMBEDDING_API_KEY=ollama

For OpenAI:
    EMBEDDING_PROVIDER=openai
    EMBEDDING_MODEL=text-embedding-3-small
    EMBEDDING_BASE_URL=https://api.openai.com/v1
    EMBEDDING_API_KEY=sk-...

For OpenRouter:
    EMBEDDING_PROVIDER=openai
    EMBEDDING_MODEL=openai/text-embedding-3-small
    EMBEDDING_BASE_URL=https://openrouter.ai/api/v1
    EMBEDDING_API_KEY=sk-or-...
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import numpy as np
from neo4j import GraphDatabase, basic_auth

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_EMBEDDING_PROVIDER = "ollama"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_EMBEDDING_BASE_URL = "http://localhost:11434/v1"
DEFAULT_EMBEDDING_API_KEY = "ollama"
DEFAULT_EMBEDDING_DIMENSION = 768  # nomic-embed-text. WARNING: changing this
# after the Neo4j vector index exists requires dropping and recreating the
# index (vector.dimension is fixed at index creation time). See README.
DEFAULT_SIMILARITY_THRESHOLD = 0.75

# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

# We cache the OpenAI client across calls so we don't re-create it per encode.
_embed_client = None  # (provider, model, base_url, api_key, dimension) -> client


def _get_embedding_client(config: dict):
    """Return a cached embedding function, keyed by config parameters.

    The returned ``callable`` accepts a list of strings and returns a list of
    numpy arrays (one per input string).
    """
    global _embed_client
    provider = config.get("embedding_provider", DEFAULT_EMBEDDING_PROVIDER)
    model = config.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    base_url = config.get("embedding_base_url", DEFAULT_EMBEDDING_BASE_URL)
    api_key = config.get("embedding_api_key", DEFAULT_EMBEDDING_API_KEY)
    dimension = config.get("embedding_dimension", DEFAULT_EMBEDDING_DIMENSION)

    cache_key = (provider, model, base_url, api_key, dimension)
    if _embed_client is not None and _embed_client[0] == cache_key:
        return _embed_client[1]

    if provider == "ollama":
        # Ollama uses the OpenAI-compatible API at /v1/embeddings
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key)

        def _ollama_embed(texts: list[str]) -> list[np.ndarray]:
            # Ollama /v1/embeddings accepts a single prompt or a list
            response = client.embeddings.create(model=model, input=texts)
            # Sort by index to guarantee order
            sorted_data = sorted(response.data, key=lambda d: d.index)
            return [np.array(d.embedding, dtype=np.float32) for d in sorted_data]

        _embed_client = (cache_key, _ollama_embed)
        return _ollama_embed

    elif provider == "openai":
        # OpenAI-compatible (OpenAI, OpenRouter, Azure, etc.)
        from openai import OpenAI

        client = OpenAI(base_url=base_url, api_key=api_key)

        def _openai_embed(texts: list[str]) -> list[np.ndarray]:
            response = client.embeddings.create(model=model, input=texts)
            sorted_data = sorted(response.data, key=lambda d: d.index)
            return [np.array(d.embedding, dtype=np.float32) for d in sorted_data]

        _embed_client = (cache_key, _openai_embed)
        return _openai_embed

    else:
        raise ValueError(f"Unsupported embedding provider: {provider!r}. "
                         f"Use 'ollama' or 'openai'.")


# ---------------------------------------------------------------------------
# Memory provider
# ---------------------------------------------------------------------------


class Neo4jGraphRagMemoryProvider:
    """Memory provider that stores and retrieves facts in a Neo4j graph database.

    Each conversation turn is stored as a ``Fact`` node with a vector embedding
    for semantic similarity search.  The ``prefetch`` method pulls relevant
    memories before each turn, and ``neo4j_recall`` provides explicit search.

    Configuration is read from environment variables — see module docstring
    for the full list.
    """

    def __init__(self):
        self._driver = None
        self._config = {}
        self._initialized = False

    # -- identity -----------------------------------------------------------

    @property
    def name(self) -> str:
        """Provider identifier used by the agent framework.

        ``neo4j-graphrag-memory`` is the original Hermes plugin name (backward
        compat).  This project is also known as ``neo-mem`` — either works.
        """
        return "neo4j-graphrag-memory"

    def is_available(self) -> bool:
        """Check whether the provider is configured and dependencies met."""
        try:
            import neo4j  # noqa: F401
        except ImportError:
            logger.warning("neo4j package not installed")
            return False
        try:
            self._config = self._get_config()
            return bool(
                self._config.get("uri")
                and self._config.get("user")
                and self._config.get("password")
            )
        except Exception as e:
            logger.warning(f"Neo4j config error: {e}")
            return False

    # -- config -------------------------------------------------------------

    def _get_config(self) -> dict:
        """Read configuration from Hermes config and environment variables.

        Environment variables take precedence over ``memory.neo4j`` from the
        Hermes config file.
        """
        # Try to read from Hermes config (gracefully handle absence)
        config = {}
        try:
            from hermes_cli.config import cfg_get
            cfg = cfg_get("memory.neo4j", {})
            if cfg is None:
                cfg = {}
            config.update(cfg)
        except ImportError:
            pass  # Running outside Hermes — rely on env vars only

        # Environment variable overrides
        env_map = {
            "NEO4J_URI": "uri",
            "NEO4J_USER": "user",
            "NEO4J_PASS": "password",
            "NEO4J_DATABASE": "database",
            "NEO4J_EMBEDDING_DIMENSION": "embedding_dimension",
            "NEO4J_SIMILARITY_THRESHOLD": "similarity_threshold",
            "EMBEDDING_PROVIDER": "embedding_provider",
            "EMBEDDING_MODEL": "embedding_model",
            "EMBEDDING_BASE_URL": "embedding_base_url",
            "EMBEDDING_API_KEY": "embedding_api_key",
            "EMBEDDING_DIMENSION": "embedding_dimension",
        }
        for env_var, cfg_key in env_map.items():
            value = os.getenv(env_var)
            if value is not None:
                # Convert numeric types
                if cfg_key in ("embedding_dimension",):
                    config[cfg_key] = int(value)
                elif cfg_key in ("similarity_threshold",):
                    config[cfg_key] = float(value)
                else:
                    config[cfg_key] = value

        # Fill in defaults for embedding settings
        config.setdefault("embedding_provider", DEFAULT_EMBEDDING_PROVIDER)
        config.setdefault("embedding_model", DEFAULT_EMBEDDING_MODEL)
        config.setdefault("embedding_base_url", DEFAULT_EMBEDDING_BASE_URL)
        config.setdefault("embedding_api_key", DEFAULT_EMBEDDING_API_KEY)
        config.setdefault("embedding_dimension", DEFAULT_EMBEDDING_DIMENSION)
        config.setdefault("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)

        return config

    # -- lifecycle ----------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        """Connect to Neo4j and ensure the vector index exists."""
        if self._initialized:
            return

        self._config = self._get_config()
        uri = self._config.get("uri")
        user = self._config.get("user")
        password = self._config.get("password")
        database = self._config.get("database", "neo4j")

        if not all([uri, user, password]):
            raise ValueError(
                "Neo4j URI, user, and password must be configured via "
                "NEO4J_URI, NEO4J_USER, and NEO4J_PASS environment variables "
                "or memory.neo4j in config.yaml."
            )

        try:
            embed_fn = _get_embedding_client(self._config)
            # Check that the embedding dimension matches the vector index
            test_emb = embed_fn(["dimension probe"])[0]
            if len(test_emb) != self._config.get("embedding_dimension", DEFAULT_EMBEDDING_DIMENSION):
                logger.warning(
                    f"Embedding dimension mismatch: model returned {len(test_emb)}-dim "
                    f"vectors but config/index expects "
                    f"{self._config.get('embedding_dimension', DEFAULT_EMBEDDING_DIMENSION)}-dim. "
                    "If you changed the embedding model, you must drop and recreate "
                    "the 'fact_embedding_index' vector index."
                )
            self._driver = GraphDatabase.driver(
                uri,
                auth=basic_auth(user, password),
                max_connection_lifetime=3600,
            )
            # Verify connectivity
            with self._driver.session(database=database) as session:
                session.run("RETURN 1").consume()
            logger.info(f"Connected to Neo4j at {uri}")

            self._ensure_schema(database)
            self._initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize Neo4j driver: {e}")
            self._driver = None
            raise

    def _ensure_schema(self, database: str) -> None:
        """Create the ``Fact`` node constraint and a vector index when possible."""
        with self._driver.session(database=database) as session:
            # Unique constraint on Fact.uid
            session.run(
                "CREATE CONSTRAINT fact_uid IF NOT EXISTS "
                "FOR (f:Fact) REQUIRE f.uid IS UNIQUE"
            )

            # Vector index (Neo4j 5.11+)
            dimension = self._config.get(
                "embedding_dimension", DEFAULT_EMBEDDING_DIMENSION
            )
            try:
                result = session.run("SHOW INDEXES WHERE name = 'fact_embedding_index'")
                if not list(result):
                    session.run(f"""
                        CREATE VECTOR INDEX fact_embedding_index IF NOT EXISTS
                        FOR (f:Fact) ON (f.embedding)
                        OPTIONS {{
                            indexConfig: {{
                                `vector.dimension`: {dimension},
                                `vector.similarity_function`: 'cosine'
                            }}
                        }}
                    """)
                    logger.info(
                        f"Created vector index 'fact_embedding_index' "
                        f"with dimension {dimension}"
                    )
            except Exception as e:
                logger.warning(
                    f"Could not create vector index (may need Neo4j 5.11+): {e}"
                )

    def shutdown(self) -> None:
        """Close the Neo4j driver."""
        if self._driver:
            self._driver.close()
            self._driver = None
            self._initialized = False
            logger.info("Neo4j driver closed")

    # -- tools --------------------------------------------------------------

    def system_prompt_block(self) -> str:
        return (
            "You have access to a persistent knowledge graph memory stored in Neo4j. "
            "Use the neo4j_recall tool to retrieve relevant past conversations. "
            "Memories are stored as facts with embeddings and can be retrieved "
            "by semantic similarity."
        )

    def get_tool_schemas(self) -> list[dict]:
        return [
            {
                "name": "neo4j_recall",
                "description": (
                    "Recall relevant memories from the Neo4j knowledge graph "
                    "using semantic similarity."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text to search for similar memories.",
                        },
                        "k": {
                            "type": "integer",
                            "description": "Number of results (default: 5).",
                            "default": 5,
                        },
                    },
                    "required": ["query"],
                },
            }
        ]

    def handle_tool_call(
        self, tool_name: str, args: dict, **kwargs
    ) -> str:
        if tool_name == "neo4j_recall":
            return self._neo4j_recall(args.get("query", ""), args.get("k", 5))
        raise NotImplementedError(
            f"Provider {self.name} does not handle tool {tool_name!r}"
        )

    # -- recall -------------------------------------------------------------

    def _neo4j_recall(self, query: str, k: int = 5) -> str:
        """Search the knowledge graph for facts similar to *query*."""
        if not self._driver:
            return json.dumps({"error": "Neo4j driver not initialised"})

        try:
            embed_fn = _get_embedding_client(self._config)
            query_embedding = embed_fn([query])[0].tolist()

            cypher = """
                MATCH (f:Fact)
                WITH f, f.embedding AS f_emb
                CALL db.index.vector.queryNodes('fact_embedding_index', $k, $query_embedding)
                YIELD node AS matched, score
                WHERE node:Fact
                RETURN matched, score
                ORDER BY score DESC
                LIMIT $k
            """
            with self._driver.session(
                database=self._config.get("database", "neo4j")
            ) as session:
                result = session.run(
                    cypher,
                    query_embedding=query_embedding,
                    k=k,
                )
                records = []
                for record in result:
                    f = record["matched"]
                    records.append({
                        "uid": f["uid"],
                        "summary": f["summary"],
                        "user_message": f["user_message"],
                        "assistant_message": f["assistant_message"],
                        "created_at": str(f["created_at"]),
                        "similarity": round(float(record["score"]), 3),
                    })
                return json.dumps({"results": records, "count": len(records)})

        except Exception as e:
            logger.error(f"Error in neo4j_recall: {e}")
            return json.dumps({"error": str(e)})

    # -- turn storage -------------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Store the current conversation turn as a ``Fact`` node."""
        if not self._driver:
            logger.warning("Neo4j driver not available — skipping sync_turn")
            return

        try:
            summary = (
                f"User: {user_content[:100]}...  "
                f"Assistant: {assistant_content[:100]}..."
            )
            text_for_embedding = f"{user_content} {assistant_content}"

            embed_fn = _get_embedding_client(self._config)
            embedding = embed_fn([text_for_embedding])[0].tolist()

            with self._driver.session(
                database=self._config.get("database", "neo4j")
            ) as session:
                query = """
                    CREATE (f:Fact {
                        uid: randomUUID(),
                        user_message: $user_message,
                        assistant_message: $assistant_message,
                        summary: $summary,
                        embedding: $embedding,
                        created_at: datetime(),
                        session_id: $session_id
                    })
                    RETURN f.uid AS uid
                """
                result = session.run(
                    query,
                    user_message=user_content,
                    assistant_message=assistant_content,
                    summary=summary,
                    embedding=embedding,
                    session_id=session_id,
                )
                uid = result.single()["uid"]
                logger.debug(f"Stored Fact with uid: {uid}")

        except Exception as e:
            logger.error(f"Failed to store fact in Neo4j: {e}")

    # -- prefetch -----------------------------------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return relevant memories as context for the upcoming turn.

        Called automatically before each agent turn to inject context.
        """
        if not self._driver:
            return ""
        try:
            result_json = self._neo4j_recall(query, k=3)
            result = json.loads(result_json)
            if "error" in result:
                return ""
            memories = result.get("results", [])
            if not memories:
                return ""
            lines = ["Relevant memories from knowledge graph:"]
            for mem in memories:
                lines.append(
                    f"- {mem['summary']} (similarity: {mem['similarity']:.2f})"
                )
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error in prefetch: {e}")
            return ""