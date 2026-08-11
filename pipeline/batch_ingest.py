#!/usr/bin/env python3
"""Batch Ingester — re-ingest PDFs with 768-dim Ollama embeddings."""
import os, sys, uuid, logging, argparse, time
from pathlib import Path
from datetime import datetime
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.http import models
from openai import OpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("batch-ingest")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://neo4j-pieter:7687")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
EMBED_BASE = os.getenv("EMBEDDING_BASE_URL", "http://192.168.0.200:11434/v1")
INC = Path("/srv/kb/pieter/Incoming")
ING = Path("/srv/kb/pieter/Ingested")
FAI = Path("/srv/kb/pieter/Failed")


def process_one(pdf, ec, n4j, qd):
    """Process a single PDF: extract text, chunk, embed, store."""
    import fitz
    doc = fitz.open(pdf)
    raw = "\n".join(p.get_text() for p in doc)
    doc.close()
    if not raw or len(raw.strip()) < 20:
        return 0
    chunks = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200).split_text(raw)
    if not chunks:
        return 0
    resp = ec.embeddings.create(input=chunks, model=EMBED_MODEL)
    embs = [d.embedding for d in resp.data]
    did = str(uuid.uuid4())[:8]
    du = f"doc-{did}"
    pts = []
    with n4j.session() as s:
        s.run(
            "MERGE (d:Document {uid: $u}) SET d.title = $t, d.source = $f, d.ingested_at = datetime($i)",
            u=du, t=pdf.stem, f=pdf.name, i=datetime.utcnow().isoformat()
        )
        for j, (tx, ve) in enumerate(zip(chunks, embs)):
            ci = f"{did}-{j:04d}"
            s.run(
                "MERGE (c:Chunk {uid: $u}) SET c.text = $t, c.embedding = $e, c.chunk_index = $j, c.doc_id = $d",
                u=ci, t=tx, e=ve, j=j, d=du
            )
            s.run(
                "MATCH (d:Document {uid: $du}), (c:Chunk {uid: $cu}) MERGE (d)-[:HAS_CHUNK]->(c)",
                du=du, cu=ci
            )
            pts.append(models.PointStruct(
                id=str(uuid.uuid4()), vector=ve,
                payload={"text": tx, "doc_id": did, "chunk_index": j, "source": pdf.name}
            ))
    if qd and pts:
        qd.upsert("documents", points=pts)
    return len(chunks)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=None, help="Max PDFs to process")
    p.add_argument("--watch", action="store_true", help="Poll every 30s")
    args = p.parse_args()

    ec = OpenAI(api_key="ollama", base_url=EMBED_BASE)
    n4j = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", "Erna#26neo4j"))
    qd = QdrantClient(QDRANT_URL, timeout=60)

    # Ensure Qdrant collection
    try:
        c = qd.get_collection("documents")
        if c.config.params.vectors.size != 768:
            qd.delete_collection("documents")
            qd.recreate_collection(
                "documents",
                vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE)
            )
    except:
        qd.recreate_collection(
            "documents",
            vectors_config=models.VectorParams(size=768, distance=models.Distance.COSINE)
        )

    tv = ec.embeddings.create(input="test", model=EMBED_MODEL)
    log.info(f"Embeddings: {EMBED_MODEL} ({len(tv.data[0].embedding)}-dim)")

    for d in [INC, ING, FAI]:
        d.mkdir(parents=True, exist_ok=True)

    remaining = args.max
    while True:
        pdfs = sorted(INC.glob("*.pdf"))
        if args.watch and not pdfs:
            log.info("Nothing in Incoming, sleeping 30s...")
            time.sleep(30)
            continue

        log.info(f"Found {len(pdfs)} in Incoming/")
        for pdf in pdfs:
            if remaining is not None and remaining <= 0:
                break
            try:
                n = process_one(pdf, ec, n4j, qd)
                pdf.rename(ING / pdf.name)
                log.info(f"  {pdf.name}: {n} chunks")
                if remaining is not None:
                    remaining -= 1
            except Exception as e:
                log.error(f"  {pdf.name}: {e}")
                pdf.rename(FAI / pdf.name)

        if not args.watch:
            break
        time.sleep(30)

    n4j.close()
    log.info("Done")


if __name__ == "__main__":
    main()