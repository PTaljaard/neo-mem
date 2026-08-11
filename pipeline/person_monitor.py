#!/usr/bin/env python3
"""Person Monitor — search for commentary by a person and store in Neo4j as NewsArticle + Claim nodes.

Usage:
  python person_monitor.py "Dawie Roodt"
  python person_monitor.py "Frans Cronje" --days 14
  python person_monitor.py "Azar Jammine" --dry-run
"""
import os, sys, json, uuid, hashlib, argparse, datetime, time, re
from neo4j import GraphDatabase
from openai import OpenAI

NEO4J_URI = "bolt://192.168.0.114:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "Erna#26neo4j"
EMBED_MODEL = "nomic-embed-text"
EMBED_BASE = "http://192.168.0.200:11434/v1"
CHAT_MODEL = "gemma4:e4b-it-qat"
CHAT_BASE = "http://192.168.0.200:11434/v1"

log = print


def parse_date(date_str):
    """Parse RSS date to ISO format for Neo4j."""
    import email.utils
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.isoformat()
    except:
        return datetime.datetime.utcnow().isoformat()


def normalize_name(name):
    """Normalize person name for Neo4j Person node."""
    return name.strip().title()


def repair_json(raw):
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
    s, e = raw.find('{'), raw.rfind('}')
    if s == -1 or e == -1:
        return {"claims": []}
    raw = raw[s:e+1]
    try:
        return json.loads(raw)
    except:
        raw = re.sub(r',\s*}', '}', raw)
        raw = re.sub(r',\s*]', ']', raw)
        try:
            return json.loads(raw)
        except:
            return {"claims": []}


def search_commentary(person, days=7):
    """Search for recent commentary by a person. Returns list of article dicts."""
    import urllib.request
    import xml.etree.ElementTree as ET

    queries = [
        f'"{person}" commentary OR interview OR analysis OR opinion {datetime.date.today().isoformat()}',
        f'"{person}" South Africa economist OR strategist OR analyst',
    ]
    articles = []
    seen_urls = set()

    for query in queries:
        try:
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-ZA&gl=ZA"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=15)
            tree = ET.parse(resp)
            root = tree.getroot()
            for item in root.findall(".//item"):
                title = item.findtext("title", "")
                link = item.findtext("link", "")
                desc = item.findtext("description", "")
                pub = item.findtext("pubDate", "")
                source = item.findtext("source", "")
                if link and link not in seen_urls:
                    seen_urls.add(link)
                    articles.append({
                        "title": title.strip(),
                        "description": desc.strip(),
                        "url": link.strip(),
                        "source": source or "Google News",
                        "published_at": pub,
                        "category": "commentary",
                        "person": person
                    })
        except Exception as e:
            log(f"  ⚠️ Search failed: {e}")

    return articles[:5]


def extract_claims(text, llm, count=3):
    """Extract claims from text."""
    if not text or len(text.strip()) < 50:
        return [], []
    prompt = f"""Extract up to {count} factual claims or key opinions from this commentary.
Keep claims SHORT (under 20 words). Return ONLY valid JSON. No markdown, no code fences.
{{"claims":[{{"claim":"short text","confidence":0.0}}]}}
Text: {text[:4000]}"""
    try:
        resp = llm.chat.completions.create(
            model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=512
        )
        data = repair_json(resp.choices[0].message.content)
        claims = [c for c in data.get("claims", []) if isinstance(c, dict) and "claim" in c][:count]
        return [c["claim"] for c in claims], [c.get("confidence", 0.5) for c in claims]
    except Exception as e:
        log(f"  ⚠️ Claim extraction failed: {e}")
        return [], []


def store_in_neo4j(n4j, emb_client, article, claims_texts, claims_confs, dry_run=False):
    """Store as NewsArticle + Claim nodes, linked to Person."""
    uid = hashlib.md5(article["url"].encode()).hexdigest()[:16]
    person = normalize_name(article["person"])

    with n4j.session() as s:
        # Check dedup
        exists = s.run("MATCH (n:NewsArticle {uid: $u}) RETURN n LIMIT 1", u=uid).single()
        if exists:
            log(f"  ⏭️ Duplicate: {article['title'][:50]}")
            return False

        # Ensure Person node exists
        s.run("MERGE (p:Person {name: $n}) SET p.role = 'Commentator', p.updated_at = datetime()", n=person)

        # Embed
        embed_text = f"{article['title']}. {article['description']}"
        try:
            resp = emb_client.embeddings.create(input=embed_text[:8000], model=EMBED_MODEL)
            embedding = resp.data[0].embedding
        except:
            embedding = []

        if dry_run:
            log(f"  📝 DRY RUN: Would create NewsArticle + {len(claims_texts)} claims for {article['title'][:50]}")
            return True

        # Create NewsArticle
        s.run("""
            MERGE (n:NewsArticle {uid: $u})
            SET n.title = $t, n.summary = $s, n.url = $u2, n.source = $src,
                n.category = $cat, n.published_at = datetime($pub), n.fetched_at = datetime(),
                n.embedding = $emb, n.claim_count = $cc
        """, u=uid, t=article["title"][:500], s=article["description"][:1000],
             u2=article["url"], src=article["source"], cat=article["category"],
             pub=parse_date(article.get("published_at", "")),
             emb=embedding, cc=len(claims_texts))

        # Link to Person
        s.run("MATCH (n:NewsArticle {uid: $u}), (p:Person {name: $n}) MERGE (n)-[:MENTIONS]->(p)",
              u=uid, n=person)

        # Create Claim nodes
        for i, (ct, cf) in enumerate(zip(claims_texts, claims_confs)):
            cuid = f"{uid}-c{i}"
            s.run("MERGE (c:Claim {uid: $u}) SET c.text = $t, c.confidence = $cf, c.extracted_at = datetime(), c.source_uid = $su",
                  u=cuid, t=ct, cf=cf, su=uid)
            s.run("MATCH (n:NewsArticle {uid: $nu}), (c:Claim {uid: $cu}) MERGE (n)-[:CONTAINS_CLAIM]->(c)",
                  nu=uid, cu=cuid)

        log(f"  ✅ Stored: {article['title'][:50]} → {len(claims_texts)} claims, linked to {person}")
        return True


def main():
    p = argparse.ArgumentParser(description="Monitor commentary by a person and store in Neo4j")
    p.add_argument("person", help="Person name to search for (e.g. 'Dawie Roodt')")
    p.add_argument("--days", type=int, default=7, help="Search window in days")
    p.add_argument("--dry-run", action="store_true", help="Print what would be done without storing")
    p.add_argument("--claims", type=int, default=3, help="Number of claims to extract per article")
    args = p.parse_args()

    log(f"🔍 Monitoring: {args.person} (last {args.days} days)")
    emb = OpenAI(api_key="ollama", base_url=EMBED_BASE)
    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

    articles = search_commentary(args.person, args.days)
    log(f"  Found {len(articles)} articles")

    stored = 0
    for article in articles:
        text = f"{article['title']}. {article['description']}"
        claims, confs = extract_claims(text, emb, args.claims)
        if store_in_neo4j(n4j, emb, article, claims, confs, args.dry_run):
            stored += 1
        time.sleep(1)  # Rate limit

    log(f"\n✅ Done: {stored} articles stored for {args.person}")
    n4j.close()


if __name__ == "__main__":
    main()