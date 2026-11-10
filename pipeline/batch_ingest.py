#!/usr/bin/env python3
"""Batch Ingester — re-ingest PDFs/ODT/Images with 768-dim Ollama embeddings."""
import os, sys, uuid, logging, argparse, time, re
from pathlib import Path
from datetime import datetime
from neo4j import GraphDatabase
from qdrant_client import QdrantClient
from qdrant_client.http import models
from openai import OpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("batch-ingest")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://192.168.1.20:7687")
QDRANT_URL = os.getenv("QDRANT_URL", "http://192.168.1.20:6333")
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text:latest")
EMBED_BASE = os.getenv("OLLAMA_BASE_URL", "http://100.118.200.88:11434/v1")
INC = Path("/home/pt/neo-mem-wsl/docs/kb/pieter/Incoming")
ING = Path("/home/pt/neo-mem-wsl/docs/kb/pieter/Ingested")
FAI = Path("/home/pt/neo-mem-wsl/docs/kb/pieter/Failed")


def extract_text_from_file(file_path: Path) -> str:
    """
    Detect file type and extract raw string content.
    Supports PDF, ODT, Images (for OCR), plus Markdown/plain-text/docs.
    """
    ext = file_path.suffix.lower()
    if ext == ".md":
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        # Strip common Markdown markup for cleaner text
        raw = re.sub(r"```.*?```", " ", raw, flags=re.S)
        raw = re.sub(r"[#>*`~|]", " ", raw)
        raw = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", raw)
        raw = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", raw)
        raw = re.sub(r"!\[\[[^\]]*\]\]", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        return raw

    elif ext == ".txt":
        return file_path.read_text(encoding="utf-8", errors="replace").strip()

    elif ext == ".docx":
        # python-docx
        try:
            import docx
            d = docx.Document(str(file_path))
            return "\n".join(p.text for p in d.paragraphs).strip()
        except Exception as e:
            log.error(f"DOCX extraction failed for {file_path.name}: {e}")
            return ""

    elif ext == ".pdf":
        import fitz
        doc = fitz.open(file_path)
        raw = " ".join(p.get_text() for p in doc).strip()
        doc.close()
        return raw

    elif ext == ".odt":
        # odfpy usage
        try:
            from odf import text, teletype
            from odf.opendocument import load
            doc = load(file_path)
            paragraphs = doc.getElementsByType(text.P)
            return "\n".join(teletype.extractText(p) for p in paragraphs)
        except Exception as e:
            log.error(f"ODT extraction failed for {file_path.name}: {e}")
            return ""

    elif ext in [".jpg", ".jpeg", ".png", ".tiff", ".bmp"]:
        # Tesseract OCR
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(file_path)
            return pytesseract.image_to_string(img)
        except Exception as e:
            log.error(f"OCR failed for {file_path.name}: {e}")
            return ""

    else:
        log.warning(f"Unsupported file type: {ext}")
        return ""


def process_one(file_path, ec, n4j, qd):
    """Process a single file: extract text, chunk, embed, store (idempotent).

    Idempotency: Document UID is derived deterministically from the source
    filename, so re-ingesting the same file maps to the same Document node.
    Before writing, any existing Document + Chunk nodes for this source are
    DETACH DELETEd (and their Qdrant points dropped), then recreated from the
    current file. This guarantees re-ingestion updates/replaces rather than
    accumulating duplicates. Fact/memory + ontology layers are NOT touched.
    """
    raw = extract_text_from_file(file_path)
    if not raw or len(raw.strip()) < 20:
        return 0
    chunks = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200).split_text(raw)
    if not chunks:
        return 0
    resp = ec.embeddings.create(input=chunks, model=EMBED_MODEL)
    embs = [d.embedding for d in resp.data]
    # Deterministic Document UID from filename (idempotent re-ingest)
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", file_path.stem)[:48].lower()
    du = f"doc-{slug}"
    pts = []
    with n4j.session() as s:
        # Delete existing Document + its Chunks for this source (replace, not duplicate)
        s.run(
            "MATCH (d:Document {uid: $du}) "
            "OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk) "
            "DETACH DELETE d, c", du=du
        )
        # Also drop any legacy docs that referenced this exact filename
        s.run(
            "MATCH (d:Document {source: $f}) WHERE d.uid <> $du "
            "OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk) "
            "DETACH DELETE d, c", f=file_path.name, du=du
        )
        s.run(
            "MERGE (d:Document {uid: $u}) SET d.title = $t, d.source = $f, d.ingested_at = datetime($i)",
            u=du, t=file_path.stem, f=file_path.name, i=datetime.utcnow().isoformat()
        )
        for j, (tx, ve) in enumerate(zip(chunks, embs)):
            ci = f"{du}-{j:04d}"
            s.run(
                "MERGE (c:Chunk {uid: $u}) SET c.text = $t, c.embedding = $e, c.chunk_index = $j, c.doc_id = $d",
                u=ci, t=tx, e=ve, j=j, d=du
            )
            s.run(
                "MATCH (d:Document {uid: $du}), (c:Chunk {uid: $cu}) MERGE (d)-[:HAS_CHUNK]->(c)",
                du=du, cu=ci
            )
            pts.append(models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{du}-{j:04d}")),
                vector=ve,
                payload={"text": tx, "doc_id": du, "chunk_index": j, "source": file_path.name}
            ))
    if qd and pts:
        # Drop old points for this source document, then upsert new ones
        qd.delete("documents", points_selector=models.FilterSelector(
            filter=models.Filter(must=[models.FieldCondition(
                key="doc_id", match=models.MatchValue(value=du))])))
        qd.upsert("documents", points=pts)
    return len(chunks)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max", type=int, default=None, help="Max PDFs to process")
    p.add_argument("--watch", action="store_true", help="Poll every 30s")
    p.add_argument("--sleep", type=float, default=3.0, help="Seconds to sleep between files (default 3.0)")
    args = p.parse_args()

    ec = OpenAI(api_key="ollama", base_url=EMBED_BASE)
    n4j = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", "Erna#26neo4j"))
    qd = QdrantClient(QDRANT_URL, timeout=60, check_compatibility=False)

    # Ensure Qdrant collection
    try:
        c = qd.get_collection("documents")
        if c.config.params.vectors.size != 1024:
            qd.delete_collection("documents")
            qd.recreate_collection(
                "documents",
                vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE)
            )
    except:
        qd.recreate_collection(
            "documents",
            vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE)
        )

    tv = ec.embeddings.create(input="test", model=EMBED_MODEL)
    log.info(f"Embeddings: {EMBED_MODEL} ({len(tv.data[0].embedding)}-dim)")

    for d in [INC, ING, FAI]:
        d.mkdir(parents=True, exist_ok=True)

    remaining = args.max
    while True:
        # Support Markdown, plain text, DOCX, PDF, ODT, and common image formats (case-insensitive)
        patterns = ["*.[pP][dD][fF]", "*.[oO][dD][tT]", "*.[jJ][pP][gG]", "*.[jJ][pP][eE][gG]", "*.[pP][nN][gG]", "*.[tT][iI][fF][fF]", "*.[bB][mM][pP]", "*.[mM][dD]", "*.[tT][xX][tT]", "*.[dD][oO][cC][xX]"]
        files = []
        for pattern in patterns:
            files.extend(INC.glob(pattern))
        files = sorted(files)
        
        if args.watch and not files:
            log.info("Nothing in Incoming, sleeping 30s...")
            time.sleep(30)
            continue

        log.info(f"Found {len(files)} in Incoming/")
        for file_path in files:
            if remaining is not None and remaining <= 0:
                break
            try:
                n = process_one(file_path, ec, n4j, qd)
                # Check file still exists before rename (may have been moved by concurrent run)
                if file_path.exists():
                    file_path.rename(ING / file_path.name)
                log.info(f"  {file_path.name}: {n} chunks")
                if remaining is not None:
                    remaining -= 1
                time.sleep(args.sleep)
            except Exception as e:
                log.error(f"  {file_path.name}: {e}")
                if file_path.exists():
                    file_path.rename(FAI / file_path.name)

        if not args.watch:
            break
        time.sleep(30)

    n4j.close()
    log.info("Done")


if __name__ == "__main__":
    main()