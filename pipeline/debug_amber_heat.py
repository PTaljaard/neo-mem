#!/usr/bin/env python3
"""Debug: why does Amber heat article get 0 claims?
Fetch its body text and test claim extraction directly."""
from neo4j import GraphDatabase
from openai import OpenAI
import re, json

n4j = GraphDatabase.driver("bolt://192.168.0.114:7687", auth=("neo4j", "Erna#26neo4j"))
llm = OpenAI(base_url="http://192.168.0.200:11434/v1", api_key="ollama")

with n4j.session() as s:
    r = s.run("""
        MATCH (n:NewsArticle)
        WHERE size(n.body) > 100 AND (n.claim_count IS NULL OR n.claim_count = 0)
        RETURN n.uid, n.title, n.body
        LIMIT 5
    """)
    for row in r:
        uid = row["n.uid"]
        title = row["n.title"]
        body = row["n.body"][:4000]
        print(f"\n📰 {title}")
        print(f"   Body length: {len(body)}")
        print(f"   First 200 chars: {body[:200]}")

        prompt = f"""Extract exactly 3 factual claims from this article.
Keep claims SHORT (under 20 words). Return ONLY valid JSON. No markdown, no code fences.
{{"claims":[{{"claim":"short text","confidence":0.0}}]}}
Article: {body}"""

        resp = llm.chat.completions.create(
            model="gemma4:e4b-it-qat",
            messages=[{"role":"user","content":prompt}],
            temperature=0.1, max_tokens=512
        )
        content = resp.choices[0].message.content.strip()
        print(f"   Response ({len(content)} chars): {content[:200]}")

        # Parse
        content = re.sub(r'```(?:json)?\s*', '', content).strip()
        s2, e2 = content.find('{'), content.rfind('}')
        if s2 >= 0 and e2 > s2:
            data = json.loads(content[s2:e2+1])
            claims = data.get("claims", [])
            print(f"   Claims: {len(claims)}")
            for c in claims:
                print(f"     - {c.get('claim','?')[:80]} (conf: {c.get('confidence',0)})")
        else:
            print(f"   ❌ No JSON delimiters found. Raw: {content[:300]}")

n4j.close()