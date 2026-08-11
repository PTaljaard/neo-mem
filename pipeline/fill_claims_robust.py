#!/usr/bin/env python3
"""Quick claim extraction for today's news articles with robust JSON handling."""
import re, json, time
from neo4j import GraphDatabase
from openai import OpenAI

NEO4J_URI = "bolt://192.168.0.114:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "Erna#26neo4j"
CHAT_MODEL = "gemma4:e4b-it-qat"
CHAT_BASE = "http://192.168.0.200:11434/v1"
CLAIM_COUNT = 3

def repair_json(raw):
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
    start, end = raw.find('{'), raw.rfind('}')
    if start == -1 or end == -1:
        return {"claims": []}
    raw = raw[start:end+1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    raw = re.sub(r',\s*}', '}', raw)
    raw = re.sub(r',\s*]', ']', raw)
    raw = re.sub(r"(?<!\\)'", '"', raw)
    raw = re.sub(r'(\w+):', r'"\1":', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"claims": []}

n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
llm = OpenAI(base_url=CHAT_BASE, api_key="ollama")

with n4j.session() as s:
    result = s.run("""
        MATCH (n:NewsArticle)
        WHERE n.claims IS NULL OR size(n.claims) = 0
        RETURN n.uid, n.title, n.body, n.summary
        LIMIT 10
    """)
    articles = [{"uid": r["n.uid"], "title": r["n.title"] or "", "body": r["n.body"] or "", "summary": r["n.summary"] or ""} for r in result]
    print(f"Processing {len(articles)} articles")

    for a in articles:
        text = a["body"] or f"{a['title']}. {a['summary']}"
        if not text.strip() or len(text) < 50:
            continue
        prompt = f"""Extract the {CLAIM_COUNT} most important factual claims from this news article.
Keep claims SHORT (under 20 words each). Return ONLY valid JSON. No markdown, no code fences.
{{"claims": [{{"claim": "short text", "confidence": 0.0}}]}}
Article: {text[:4000]}"""
        for attempt in range(3):
            try:
                resp = llm.chat.completions.create(
                    model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}],
                    temperature=0.1, max_tokens=512
                )
                data = repair_json(resp.choices[0].message.content.strip())
                claims = data.get("claims", [])
                texts = [c["claim"] for c in claims if isinstance(c, dict) and "claim" in c][:CLAIM_COUNT]
                confs = [c["confidence"] for c in claims if isinstance(c, dict) and "confidence" in c][:CLAIM_COUNT]
                if texts:
                    # Create Claim nodes and link them
                    for i, (ct, cf) in enumerate(zip(texts, confs)):
                        cuid = f"{a['uid']}-c{i}"
                        s.run("MERGE (c:Claim {uid: $u}) SET c.text = $t, c.confidence = $cf, c.extracted_at = datetime(), c.source_uid = $su",
                              u=cuid, t=ct, cf=cf, su=a['uid'])
                        s.run("MATCH (n:NewsArticle {uid: $nu}), (c:Claim {uid: $cu}) MERGE (n)-[:CONTAINS_CLAIM]->(c)",
                              nu=a['uid'], cu=cuid)
                    s.run("MATCH (n:NewsArticle {uid: $u}) SET n.claim_count = $cc", u=a['uid'], cc=len(texts))
                    print(f"  ✅ {a['title'][:50]}: {len(texts)} claims")
                    break
            except Exception as e:
                if attempt == 2:
                    print(f"  ❌ {a['title'][:50]}: {e}")
                time.sleep(1)

n4j.close()
print("Done")