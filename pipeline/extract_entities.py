#!/usr/bin/env python3
"""Rabust entity/fact extraction from Neo4j chunks using LLM with retry & JSON repair."""
import os, sys, uuid, logging, json, re, time, random, subprocess
from neo4j import GraphDatabase
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("extract-robust")

# ── Config ────────────────────────────────────────────────────────────
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://192.168.1.20:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASSWORD", "Erna#26neo4j")
# OmniRoute gateway on R9, reachable from other hosts (D7/WSL) via Tailscale IP.
# (169.254.27.18:20128 is NOT routable from D7; use the Tailscale mesh IP 100.118.200.88.)
# In WSL2 mirrored mode, R9 Windows/WSL share IP 169.254.27.18; localhost is shared.
OMNIROUTE_BASE = os.getenv("OMNIROUTE_BASE_URL", "http://100.118.200.88:20128/v1")
EXTRACT_MODEL = os.getenv("EXTRACT_MODEL", "auto/best-free")


def _load_omniroute_key():
    """Return the OmniRoute API key.

    OmniRoute runs on R9 (169.254.27.18 in mirrored mode). Precedence:
      1. OMNIROUTE_API_KEY env var (explicit)
      2. HERMES_CUSTOM_LOCALHOST_20128_API_KEY env var (Hermes' own gateway key)
      3. This host's Hermes .env / config.yaml
      4. R9's Hermes .env over the mesh (when run from D7/WSL)
    Falls back to 'ollama' only if none available.
    """
    for var in ("OMNIROUTE_API_KEY", "HERMES_CUSTOM_LOCALHOST_20128_API_KEY"):
        v = os.getenv(var)
        if v and len(v) > 10:
            return v
    local = [
        os.path.expanduser("~/.hermes/.env"),
        "/home/pt/.hermes/.env",
        "/home/pt/.hermes/config.yaml",
        os.path.expanduser("~/.hermes/config.yaml"),
    ]
    for p in local:
        try:
            lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
            for line in lines:
                m = re.match(r"\s*(?:export\s+)?(?:OMNIROUTE_API_KEY|HERMES_CUSTOM_LOCALHOST_20128_API_KEY)\s*=\s*['\"]?([^'\"]+)", line)
                if m and len(m.group(1)) > 10:
                    return m.group(1).strip()
                if re.match(r"^\s*api_key\s*:", line):
                    val = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if val and "ollama" not in val:
                        return val
        except FileNotFoundError:
            continue
    # Pull from R9 over the mesh (passwordless SSH aliases available)
    for host in ("r9lg", "r9"):
        try:
            out = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                 "grep -E 'HERMES_CUSTOM_LOCALHOST_20128_API_KEY|^[[:space:]]*api_key' /home/pt/.hermes/.env /home/pt/.hermes/config.yaml 2>/dev/null | head -2 | sed -E 's/.*=[[:space:]]*//; s/.*:[[:space:]]*//' | tr -d '\\\"' | grep -vi ollama"],
                capture_output=True, text=True, timeout=20,
            ).stdout.strip().splitlines()
            if out:
                for line in out:
                    if len(line) > 10:
                        return line
        except Exception:
            continue
    return "ollama"


API_KEY = os.getenv("OMNIROUTE_API_KEY") or _load_omniroute_key()
MAX_RETRIES = 3

# ── Helpers ───────────────────────────────────────────────────────────

def repair_json(raw: str) -> dict:
    """Attempt to repair and parse JSON from LLM output."""
    # Strip markdown code fences
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
    # Find first { and last }
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1:
        return {"entities": [], "facts": []}
    raw = raw[start:end+1]
    # Try standard parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Fix common issues: trailing commas, single quotes, unquoted keys
    raw = re.sub(r',\s*}', '}', raw)  # trailing commas
    raw = re.sub(r',\s*]', ']', raw)  # trailing commas in arrays
    raw = re.sub(r"(?<!\\)'", '"', raw)  # single → double quotes
    raw = re.sub(r'(\w+):', r'"\1":', raw)  # unquoted keys
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"entities": [], "facts": []}

# ── Retry helper with exponential backoff for OmniRoute 503/429 ──────────
def call_with_backoff(llm, messages, max_retries=5, model="model"):
    """Call LLM with exponential backoff on 503/429 (rate limit)."""
    for attempt in range(max_retries):
        try:
            return llm.chat.completions.create(model=model, messages=messages, temperature=0.1, max_tokens=1024)
        except Exception as e:
            if "503" in str(e) or "429" in str(e):
                wait = (2 ** attempt) + random.uniform(0, 1)
                log(f"  ⚠️ Rate limited ({e.__class__.__name__}), waiting {wait:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Max retries ({max_retries}) exceeded for {model}")

def extract_entities_and_facts(llm, text, retries=MAX_RETRIES):
    """Extract with retry on failure."""
    prompt = f"""Extract entities and facts from this text about the DIAL-KG paper.

Return ONLY valid JSON with this exact structure:
{{"entities": [{{"name": "...", "type": "PAPER|METHOD|CONCEPT|DATASET|PERSON|METRIC|TASK|SYSTEM"}}],
 "facts": [{{"subject": "...", "relation": "relation_verb", "object": "..."}}]}}

Rules:
- entities: unique named things. Use full canonical names.
- facts: subject-relation-object triples explicitly stated in the text.
- relation: lowercase with underscores (e.g. proposes, achieves, improves_upon).
- Max 15 entities, 10 facts per chunk.
- If none found, return {{"entities": [], "facts": []}}

Text:
{text[:1500]}"""

    for attempt in range(retries):
        try:
            resp = call_with_backoff(llm, [{"role": "user", "content": prompt}], model=EXTRACT_MODEL)
            content = resp.choices[0].message.content
            if content is None or not content.strip():
                # Empty/None content — likely a rate-limit or blank response. Retry.
                log.warning(f"Attempt {attempt+1}: empty LLM content, retrying")
                time.sleep(2)
                continue
            data = repair_json(content)
            if "entities" in data and "facts" in data:
                # Deduplicate by name
                seen = set()
                unique = []
                for e in data["entities"]:
                    if isinstance(e, dict) and "name" in e and e["name"] not in seen:
                        seen.add(e["name"])
                        unique.append(e)
                # Filter facts where subject/object exist in entities
                valid_facts = []
                for f in data["facts"]:
                    if isinstance(f, dict) and "subject" in f and "object" in f and "relation" in f:
                        if f["subject"] in seen or f["object"] in seen:
                            valid_facts.append(f)
                return unique, valid_facts
        except Exception as ex:
            log.warning(f"Attempt {attempt+1} failed: {ex}")
            time.sleep(1)
    return [], []

def extract_batch(llm, chunks, batch_size=5, retries=MAX_RETRIES):
    """Extract entities/facts for multiple chunks in ONE LLM call (batching).

    Returns list of (chunk_uid, entities, facts) tuples. Uses a JSON array
    response keyed by input order. Falls back gracefully on partial failure.
    """
    results = []
    text_blocks = "\n\n---CHUNK---\n\n".join(
        f"[{i}] {c['text'][:1500]}" for i, c in enumerate(chunks)
    )
    prompt = f"""Extract entities and facts from EACH of the {len(chunks)} text blocks below.
For EACH block return one JSON object. Respond with ONLY a JSON array, one element per block, in the SAME order as the input.
Each element: {{"entities": [{{"name":"...","type":"PAPER|METHOD|CONCEPT|DATASET|PERSON|METRIC|TASK|SYSTEM"}}], "facts": [{{"subject":"...","relation":"relation_verb","object":"..."}}]}}
If a block has nothing, return {{"entities": [], "facts": []}} for it.
Rules: entities are unique named things (full canonical names); facts are explicit subject-relation-object triples; max 15 entities / 10 facts per block; relation lowercase with underscores.

Text blocks:
{text_blocks}"""

    for attempt in range(retries):
        try:
            resp = call_with_backoff(llm, [{"role": "user", "content": prompt}], model=EXTRACT_MODEL)
            content = resp.choices[0].message.content
            if content is None or not content.strip():
                log.warning(f"  batch attempt {attempt+1}: empty LLM content, retrying")
                time.sleep(2)
                continue
            data = repair_json(content)
            if isinstance(data, list) and len(data) == len(chunks):
                for i, chunk in enumerate(chunks):
                    item = data[i] or {}
                    ents = item.get("entities", []) if isinstance(item, dict) else []
                    facs = item.get("facts", []) if isinstance(item, dict) else []
                    # dedupe entities by name
                    seen = set()
                    uniq = []
                    for e in ents:
                        if isinstance(e, dict) and e.get("name") and e["name"] not in seen:
                            seen.add(e["name"]); uniq.append(e)
                    vf = [f for f in facs if isinstance(f, dict) and f.get("subject") and f.get("object") and f.get("relation")]
                    results.append((chunk["uid"], uniq, vf))
                return results
            else:
                log.warning(f"  batch attempt {attempt+1}: malformed response shape, retrying")
                time.sleep(2)
        except Exception as ex:
            log.warning(f"  batch attempt {attempt+1} failed: {ex}")
            time.sleep(2)
    return results

def store_in_neo4j(n4j, chunk_uid, entities, facts):
    """Atomic batch store."""
    with n4j.session() as s:
        tx = s.begin_transaction()
        try:
            for ent in entities:
                eid = f"ent-{uuid.uuid4().hex[:8]}"
                tx.run("MERGE (e:Entity {name: $n}) SET e.type = $t, e.uid = COALESCE(e.uid, $u)",
                       n=ent["name"], t=ent["type"], u=eid)
                tx.run("MATCH (c:Chunk {uid: $cu}), (e:Entity {name: $en}) MERGE (c)-[:MENTIONS]->(e)",
                       cu=chunk_uid, en=ent["name"])
            for fact in facts:
                fid = f"fact-{uuid.uuid4().hex[:8]}"
                ft = f"{fact['subject']} {fact['relation']} {fact['object']}"
                tx.run("MERGE (f:Fact {uid: $u}) SET f.text = $t", u=fid, t=ft)
                tx.run("MATCH (c:Chunk {uid: $cu}), (f:Fact {uid: $fu}) MERGE (c)-[:CONTAINS_FACT]->(f)",
                       cu=chunk_uid, fu=fid)
                tx.run("MATCH (f:Fact {uid: $fu}), (s:Entity {name: $sn}) MERGE (f)-[:HAS_SUBJECT]->(s)",
                       fu=fid, sn=fact["subject"])
                tx.run("MATCH (f:Fact {uid: $fu}), (o:Entity {name: $on}) MERGE (f)-[:HAS_OBJECT]->(o)",
                       fu=fid, on=fact["object"])
                tx.run("MATCH (s:Entity {name: $sn}), (o:Entity {name: $on}) MERGE (s)-[:RELATED_TO]-(o)",
                       sn=fact["subject"], on=fact["object"])
            tx.commit()
        except Exception:
            tx.rollback()
            raise

def main():
    import argparse
    p = argparse.ArgumentParser(description="LLM entity/fact extraction from Neo4j chunks (OmniRoute R9)")
    p.add_argument("doc_uid", nargs="?", help="Single Document UID to extract. Omit (or pass --all) to process every Document.")
    p.add_argument("--all", action="store_true", help="Process all Documents that have chunks")
    p.add_argument("--limit", type=int, default=None, help="Max chunks to process (per doc)")
    p.add_argument("--batch-size", type=int, default=5, help="Docs per batch before pausing")
    p.add_argument("--pause", type=int, default=600, help="Seconds to pause between doc batches (default 600 = 10 min)")
    p.add_argument("--max-batches", type=int, default=None, help="Stop after N batches (for testing)")
    args = p.parse_args()

    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OMNIROUTE_BASE, api_key=API_KEY)

    # Resolve which documents to process
    doc_list = []
    with n4j.session() as s:
        if args.all or not args.doc_uid:
            result = s.run(
                "MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk) "
                "RETURN d.uid AS u, COUNT(c) AS n "
                "ORDER BY n DESC"
            )
            doc_list = [{"uid": r["u"], "n": r["n"]} for r in result]
        else:
            result = s.run(
                "MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c:Chunk) "
                "RETURN d.uid AS u, COUNT(c) AS n", u=args.doc_uid
            )
            doc_list = [{"uid": r["u"], "n": r["n"]} for r in result]
    if not doc_list:
        log.info("No documents found to process")
        n4j.close()
        return
    log.info(f"Processing {len(doc_list)} documents")

    grand_e, grand_f = 0, 0
    batch_docs_processed = 0
    for doc_idx, doc in enumerate(doc_list):
        du = doc["uid"]
        log.info(f"\n=== Document {doc_idx+1}/{len(doc_list)}: {du} ({doc['n']} chunks) ===")

        # Get chunks for this doc
        with n4j.session() as s:
            result = s.run(
                "MATCH (d:Document {uid: $u})-[:HAS_CHUNK]->(c:Chunk) "
                "RETURN c.uid, c.text, c.chunk_index ORDER BY c.chunk_index",
                u=du
            )
            chunks = [{"uid": r["c.uid"], "text": r["c.text"], "idx": r["c.chunk_index"]} for r in result]
            if args.limit:
                chunks = chunks[: args.limit]
        log.info(f"  {len(chunks)} chunks")

        # Chunks already processed (have entities)
        with n4j.session() as s:
            result = s.run(
                "MATCH (c:Chunk)-[:MENTIONS]->(e) WHERE c.doc_id = $d "
                "RETURN c.uid AS uid, COUNT(e) AS n", d=du
            )
            done = {r["uid"] for r in result}
        log.info(f"  {len(done)} chunks already processed")

        doc_e, doc_f = 0, 0
        todo = [c for c in chunks if c["uid"] not in done]
        log.info(f"  {len(todo)} chunks to process (of {len(chunks)})")

        for i, chunk in enumerate(todo):
            log.info(f"    Chunk {i+1}/{len(todo)} ({chunk['uid']})")
            entities, facts = extract_entities_and_facts(llm, chunk["text"])
            if entities or facts:
                try:
                    store_in_neo4j(n4j, chunk["uid"], entities, facts)
                    doc_e += len(entities)
                    doc_f += len(facts)
                    log.info(f"      → {len(entities)} entities, {len(facts)} facts")
                except Exception as ex:
                    log.error(f"      → STORE FAILED {chunk['uid']}: {ex}")

        grand_e += doc_e
        grand_f += doc_f
        log.info(f"  Doc {du} done: +{doc_e} entities, +{doc_f} facts")

        # Batch pause logic: every N docs, pause
        batch_docs_processed += 1
        if batch_docs_processed % args.batch_size == 0 and doc_idx + 1 < len(doc_list):
            log.info(f"\n=== Batch of {args.batch_size} docs complete. Pausing {args.pause}s... ===")
            time.sleep(args.pause)

        if args.max_batches and batch_docs_processed >= args.max_batches * args.batch_size:
            log.info(f"Reached max-batches ({args.max_batches}), stopping.")
            break

    log.info(f"\nDONE over {len(doc_list)} docs: extracted {grand_e} entities, {grand_f} facts")

    # Verify totals
    with n4j.session() as s:
        r = s.run("MATCH (c:Chunk)-[:MENTIONS]->(e) RETURN COUNT(DISTINCT e) AS e, COUNT(DISTINCT c) AS c")
        for row in r:
            log.info(f"Graph total: {row['e']} entities linked to {row['c']} chunks")
        r = s.run("MATCH (c:Chunk)-[:CONTAINS_FACT]->(f) RETURN COUNT(DISTINCT f) AS f")
        for row in r:
            log.info(f"Graph total: {row['f']} facts")

    n4j.close()

if __name__ == "__main__":
    main()