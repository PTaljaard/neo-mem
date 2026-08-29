#!/usr/bin/env python3
"""
Shared configuration for the neo-mem pipeline.
All scripts import from here instead of duplicating credentials.

Usage:
    from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, ...
"""
import os
from pathlib import Path

# ── Neo4j ──────────────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://192.168.1.20:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "Erna#26neo4j")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# ── Ollama (R9 GPU) ────────────────────────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://192.168.0.200:11434/v1")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "mxbai-embed-large")
CHAT_MODEL = os.getenv("CHAT_MODEL", "auto/best-free")
CHAT_BASE = os.getenv("CHAT_BASE_URL", "http://localhost:20128/v1")

# ── OmniRoute (R9 local gateway) ───────────────────────────────────────
OMNIROUTE_BASE = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY", "ollama")
# Free-tier model map (cost-constrained, auto combos preferred for reliability):
#   auto/best-free: best available free model with auto-fallback (recommended primary)
#   auto/best-chat: best free chat model with auto-fallback
#   oc/nemotron-3-ultra-free: best quality free (PhD/ontology) — upstream may be flaky
#   oc/deepseek-v4-flash-free: bulk/mechanical (news, claims at scale) — upstream may be flaky
CHAT_PRIMARY = "auto/best-free"          # reliable aggregate with built-in fallback
CHAT_BULK = "auto/best-chat"             # chat-optimized free aggregate
CHAT_FALLBACK = "auto/best-chaos"        # last-resort free combo

# ── D7 KB Paths ────────────────────────────────────────────────────────
KB_ROOT = Path(os.getenv("KB_ROOT", "/srv/kb/pieter"))
KB_INCOMING = KB_ROOT / "Incoming"
KB_INGESTED = KB_ROOT / "Ingested"
KB_FAILED = KB_ROOT / "Failed"
KB_RESEARCH = KB_ROOT / "research"
KB_PHD = KB_ROOT / "PhD"
KB_SCRIPTS = KB_ROOT / "scripts"

# ── Firecrawl ──────────────────────────────────────────────────────────
FIRECRAWL_KEY = os.getenv("FIRECRAWL_API_KEY", "")
FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"

# ── Defaults ────────────────────────────────────────────────────────────
DEFAULT_CLAIM_COUNT = 3
DEFAULT_MAX_ARTICLES = 6
DEFAULT_COMMENTATOR_DAYS = 7