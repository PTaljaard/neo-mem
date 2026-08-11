#!/usr/bin/env python3
"""Save research findings from a Hermes conversation to the KB as Fact nodes.

Usage (via Hermes skill):
  When a research conversation produces findings, invoke:
    python3 save_research.py --findings "Natural language summary of findings"

Or pipe in structured findings from a session.
"""
import sys, json, uuid, datetime, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE
from neo4j import GraphDatabase
from openai import OpenAI

log = print


def extract_findings_from_text(text, llm):
    """Extract structured findings from natural language text using LLM."""
    prompt = f"""Extract key research findings from this text. Return JSON:
{{"findings": [
  {{"statement": "finding text", "confidence": 0.0-1.0,
    "entities": ["Entity1", "Entity2"],
    "category": "contribution|limitation|comparison|method|result|implication"}}
]}}

Keep statements short. Include entity names mentioned.
Text: {text[:6000]}"""
    try:
        resp = llm.chat.completions.create(
            model="gemma4:e4b-it-qat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=1024
        )
        content = resp.choices[0].message.content
        content = re.sub(r'```(?:json)?\s*', '', content).strip()
        s, e = content.find('{'), content.rfind('}')
        if s >= 0 and e > s:
            return json.loads(content[s:e+1]).get("findings", [])
    except Exception as ex:
        log(f"  ⚠️ Extraction failed: {ex}")
    return []


def store_findings(n4j, findings, source_session="manual"):
    """Store findings as Fact nodes linked to entities."""
    stored = 0
    with n4j.session() as s:
        for f in findings:
            fid = f"fact-{uuid.uuid4().hex[:8]}"
            text = f["statement"][:500]
            cat = f.get("category", "result")
            conf = f.get("confidence", 0.5)
            s.run("""
                MERGE (f:Fact {uid: $u})
                SET f.text = $t, f.confidence = $cf, f.category = $cat,
                    f.created_at = datetime(), f.source_session = $src,
                    f.confidence_evidence_quality = $cf,
                    f.confidence_source_reliability = $cf,
                    f.confidence_logical_consistency = $cf,
                    f.confidence_strategic = $cf
            """, u=fid, t=text, cf=conf, cat=cat, src=source_session)

            # Link to existing entities
            for ent_name in f.get("entities", []):
                s.run("""
                    MATCH (e:Entity {name: $en})
                    WITH e
                    MATCH (f:Fact {uid: $fu})
                    MERGE (f)-[:HAS_SUBJECT]->(e)
                """, en=ent_name, fu=fid)
            stored += 1
    return stored


def main():
    import argparse
    p = argparse.ArgumentParser(description="Save research findings to the KB")
    p.add_argument("--findings", "-f", help="Research findings text to save")
    p.add_argument("--session", "-s", default="manual", help="Source session identifier")
    p.add_argument("--file", help="Read findings from a file")
    p.add_argument("--extract", action="store_true", help="Use LLM to extract findings from text")
    args = p.parse_args()

    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    llm = OpenAI(base_url=OLLAMA_BASE, api_key="ollama")

    if args.file:
        text = open(args.file).read()
    elif args.findings:
        text = args.findings
    else:
        text = sys.stdin.read()

    if args.extract:
        findings = extract_findings_from_text(text, llm)
        log(f"📝 Extracted {len(findings)} findings from text")
    else:
        # Assume input is already JSON with findings array
        try:
            data = json.loads(text)
            findings = data if isinstance(data, list) else data.get("findings", [data])
        except:
            findings = [{"statement": text, "confidence": 0.5, "entities": [], "category": "result"}]

    if not findings:
        log("❌ No findings to save")
        sys.exit(1)

    count = store_findings(n4j, findings, args.session)
    log(f"✅ Saved {count} findings to KB as Fact nodes")

    with n4j.session() as s:
        r = s.run("MATCH (f:Fact) RETURN count(f) AS total").single()
        log(f"📊 Total Fact nodes in graph: {r['total']}")

    n4j.close()


if __name__ == "__main__":
    main()