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
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://192.168.0.114:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "Erna#26neo4j")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# ── Ollama (R9 GPU) ────────────────────────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://192.168.0.200:11434/v1")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
CHAT_MODEL = os.getenv("CHAT_MODEL", "gemma4:e4b-it-qat")

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