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
# D7 Tailscale IP (reachable from both R9 and D7)
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://100.118.200.88:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "Erna#26neo4j")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# ── Ollama (R9 GPU at 192.168.0.200) ───────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://192.168.0.200:11434/v1")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text:latest")
# Use OpenRouter for chat (default Hermes model)
CHAT_MODEL = os.getenv("CHAT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
CHAT_BASE = os.getenv("CHAT_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "sk-or-...e8c5")

# ── OmniRoute (R9 local gateway) - DEPRECATED, kept for compatibility ────
OMNIROUTE_BASE = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128/v1")
OMNIROUTE_API_KEY = os.getenv("OMNIROUTE_API_KEY", "ollama")
# Free-tier model map (cost-constrained, auto combos preferred for reliability):
#   auto/best-free: best available free model with auto-fallback (recommended primary)
#   auto/best-chat: best free chat model with auto-fallback
#   oc/nemotron-3-ultra-free: best quality free (PhD/ontology) — upstream may be flaky
#   oc/deepseek-v4-flash-free: bulk/mechanical (news, claims at scale) — upstream may be flaky
CHAT_PRIMARY = "nvidia/nemotron-3-ultra-550b-a55b:free"          # reliable OpenRouter default
CHAT_BULK = "nvidia/nemotron-3-ultra-550b-a55b:free"             # same
CHAT_FALLBACK = "auto/best-chaos"        # last-resort free combo (OmniRoute)

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
RUN_EXTRACT_AFTER_EMBED = os.getenv("RUN_EXTRACT", "false") == "true"