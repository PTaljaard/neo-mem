#!/usr/bin/env python3
"""Ingest the DIAL-KG paper into Neo4j (Document → Chunk with 768-dim embeddings)."""
import os, sys, uuid, logging, json
from pathlib import Path
from datetime import datetime
from neo4j import GraphDatabase
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ingest-dialkg")

NEO4J_URI = "bolt://192.168.0.114:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "Erna#26neo4j"
EMBED_MODEL = "nomic-embed-text"
EMBED_BASE = "http://192.168.0.200:11434/v1"
PDF_PATH = "C:\\Users\\ptalj\\AppData\\Local\\Temp\\2603.20059.pdf"
PAPER_TITLE = "DIAL-KG: Schema-Free Incremental Knowledge Graph Construction via Dynamic Schema Induction and Evolution-Intent Assessment"

def extract_text(pdf_path):
    """Extract text from PDF using PyMuPDF."""
    import fitz
    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text.strip()

def chunk_text(text, chunk_size=1000, overlap=200):
    """Split text into overlapping chunks."""
    text_len = len(text)
    log.info(f"Chunking {text_len} characters")
    chunks = []
    start = 0
    while start < text_len:
        end = min(start + chunk_size, text_len)
        if end < text_len:
            # Try to break at a sentence boundary
            for sep in [". ", "\n\n", "\n", " "]:
                pos = text.rfind(sep, start + chunk_size - overlap, end)
                if pos > start + chunk_size - overlap:
                    end = pos + len(sep)
                    break
        chunk = text[start:end].strip()
        if len(chunk) > 20:
            chunks.append(chunk)
        if end >= text_len - 1:
            break
        start = end
    log.info(f"Created {len(chunks)} chunks")
    return chunks

def main():
    log.info("Starting DIAL-KG ingestion")
    
    # Connect to Neo4j
    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    ec = OpenAI(base_url=EMBED_BASE, api_key="ollama")
    
    # Extract text
    log.info(f"Extracting text from {PDF_PATH}")
    raw = extract_text(PDF_PATH)
    log.info(f"Extracted {len(raw)} characters")
    
    # Chunk
    chunks = chunk_text(raw)
    log.info(f"Created {len(chunks)} chunks")
    
    # Embed
    log.info(f"Embedding via {EMBED_MODEL} (768-dim)")
    resp = ec.embeddings.create(input=chunks, model=EMBED_MODEL)
    embs = [d.embedding for d in resp.data]
    log.info(f"Got {len(embs)} embeddings (dim={len(embs[0])})")
    
    # Store in Neo4j
    doc_id = "dial-kg-2603.20059"
    with n4j.session() as s:
        # Create Document node
        s.run(
            """MERGE (d:Document {uid: $u})
               SET d.title = $t, d.source = $f, d.ingested_at = datetime($i),
                   d.authors = $a, d.year = $y, d.arxiv_id = $x""",
            u=doc_id, t=PAPER_TITLE, f="2603.20059.pdf",
            i=datetime.utcnow().isoformat(), a="DIAL-KG Authors",
            y="2026", x="2603.20059"
        )
        log.info(f"Created Document node: {doc_id}")
        
        # Create Chunk nodes with edges
        for j, (tx, ve) in enumerate(zip(chunks, embs)):
            chunk_id = f"dial-kg-{j:04d}"
            s.run(
                """MERGE (c:Chunk {uid: $u})
                   SET c.text = $t, c.embedding = $e, c.chunk_index = $j, c.doc_id = $d""",
                u=chunk_id, t=tx, e=ve, j=j, d=doc_id
            )
            s.run(
                "MATCH (d:Document {uid: $du}), (c:Chunk {uid: $cu}) MERGE (d)-[:HAS_CHUNK]->(c)",
                du=doc_id, cu=chunk_id
            )
        
        log.info(f"Created {len(chunks)} Chunk nodes with HAS_CHUNK edges")
        
        # Verify
        result = s.run("MATCH (d:Document {uid: $u}) RETURN d.title, d.arxiv_id", u=doc_id)
        for r in result:
            log.info(f"Verified: {r['d.title'][:60]}... (arXiv:{r['d.arxiv_id']})")
        
        result = s.run("MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c) RETURN count(c) as n", u=doc_id)
        for r in result:
            log.info(f"Document has {r['n']} chunks in Neo4j")
    
    n4j.close()
    log.info("DIAL-KG ingestion complete!")

if __name__ == "__main__":
    main()