#!/usr/bin/env python3
"""News ingester — fetches daily news, scrapes full text via Firecrawl,
stores in Neo4j as :NewsArticle nodes with embeddings.
Runs as a cron job with no_agent=True (raw output to Telegram).

Schema:
  (:NewsArticle {
    uid, title, summary, body, source, url,
    fetched_at, published_at, category,
    embedding             ← 768-dim nomic-embed-text
  })
  -[:MENTIONS]->(existing entities via future NER pass)

Usage:
  python3 news_ingester.py --max-articles 6
"""
import os, sys, json, uuid, hashlib, argparse, datetime, time, re
import urllib.request
from pathlib import Path
from neo4j import GraphDatabase
from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────
NEO4J_URI = "bolt://192.168.0.114:7687"
NEO4J_USER = "neo4j"
NEO4J_PASS = "Erna#26neo4j"
EMBED_MODEL = "nomic-embed-text"
EMBED_BASE = "http://192.168.0.200:11434/v1"
FIRECRAWL_KEY = "fc-cceb0426298f473bb8cef0a512924bab"
FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"

log = print

RSS_FEEDS = {
    "world": [
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    ],
    "sa": [
        "https://www.news24.com/feeds/rss",
        "https://www.timeslive.co.za/feed/",
    ],
    "tech": [
        "https://feeds.feedburner.com/TheHackersNews",
        "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    ],
    "business": [
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ],
}

# Track Firecrawl usage for cost awareness
FIRE_CNT = 0


def fetch_rss_articles(limit=6):
    """Fetch news from free RSS feeds. Returns list of article dicts."""
    import xml.etree.ElementTree as ET
    articles = []
    for category, urls in RSS_FEEDS.items():
        for feed_url in urls:
            if len(articles) >= limit * 2:
                break
            try:
                req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=15)
                tree = ET.parse(resp)
                root = tree.getroot()
                for item in root.findall(".//item"):
                    title = item.findtext("title", "")
                    desc = item.findtext("description", "")
                    link = item.findtext("link", "")
                    pub_date = item.findtext("pubDate", datetime.datetime.utcnow().isoformat())
                    source_name = feed_url.split("/")[2].replace("www.", "")
                    if title and link:
                        articles.append({
                            "title": title.strip(),
                            "description": desc.strip(),
                            "url": link.strip(),
                            "source": source_name,
                            "category": category,
                            "published_at": pub_date,
                        })
                        if len(articles) >= limit * 2:
                            break
            except Exception as e:
                log(f"  ⚠️ RSS {feed_url}: {e}")
    return articles[:limit]


def fetch_news(limit=6):
    """Wrapper — uses RSS feeds."""
    return fetch_rss_articles(limit)


def fetch_article_text(url):
    """Fetch full article text via Firecrawl API. Returns (markdown, error)."""
    global FIRE_CNT
    payload = json.dumps({"url": url}).encode()
    req = urllib.request.Request(
        FIRECRAWL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {FIRECRAWL_KEY}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        FIRE_CNT += 1
        if data.get("success") and data.get("data", {}).get("markdown"):
            return data["data"]["markdown"], None
        return None, f"no content: {data.get('error', 'unknown')}"
    except Exception as e:
        return None, str(e)


def _parse_date(date_str):
    """Parse RSS date strings into ISO format for Neo4j."""
    import email.utils
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
        return parsed.isoformat()
    except:
        return datetime.datetime.utcnow().isoformat()


def scrape_text(url):
    """Fallback: fetch page text via urllib (no Firecrawl)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="replace")
        # Strip tags
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000], None
    except Exception as e:
        return None, str(e)


def extract_claims(text, llm, count=3):
    """Extract N key claims from text using Ollama. Returns (claims_list, confidences_list)."""
    if not text or len(text.strip()) < 50:
        return [], []
    prompt = f"""Extract the {count} most important factual claims from this news article.

For each claim, provide:
- The claim itself (a short statement of fact or causal assertion)
- Your confidence in its accuracy (0.0 to 1.0)

Return ONLY valid JSON:
{{"claims": [{{"claim": "...", "confidence": 0.0}}]}}

Rules:
- Claims must be specific, factual assertions from the article
- Include causal claims (X caused Y) and factual claims (X is Y)
- Exclude opinions, predictions, and non-factual statements
- Confidence reflects: evidence quality + source reliability + logical consistency

Article text:
{text[:4000]}"""

    try:
        resp = llm.chat.completions.create(
            model=EMBED_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=512
        )
        content = resp.choices[0].message.content.strip()
        # Parse JSON
        content = re.sub(r"```(?:json)?\s*", "", content).strip()
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(content[start:end+1])
            claims = data.get("claims", [])
            texts = [c["claim"] for c in claims if isinstance(c, dict) and "claim" in c]
            confs = [c["confidence"] for c in claims if isinstance(c, dict) and "confidence" in c]
            return texts[:count], confs[:count]
    except Exception as e:
        log(f"  ⚠️ Claim extraction failed: {e}")
    return [], []


def ingest_articles(articles, emb_client, neo4j_driver, use_firecrawl=True, claim_count=3):
    """Store news articles in Neo4j with embeddings and claim extraction.
    If use_firecrawl=True, fetches full article text for better embeddings.
    claim_count: number of claims to extract per article (default 3)."""
    count = 0
    skipped = 0
    fc_success = 0
    fc_fail = 0

    for article in articles:
        uid = hashlib.md5(article["url"].encode()).hexdigest()[:16]

        # Dedup
        with neo4j_driver.session() as session:
            exists = session.run(
                "MATCH (n:NewsArticle {uid: $uid}) RETURN n LIMIT 1", uid=uid
            ).single()
            if exists:
                skipped += 1
                continue

        title = (article["title"] or "")[:500]
        summary = (article["description"] or "")[:1000]
        body = None

        # Fetch full text via Firecrawl
        if use_firecrawl:
            text, err = fetch_article_text(article["url"])
            if text:
                body = text[:10000]  # cap at 10K chars
                fc_success += 1
            else:
                fc_fail += 1
                log(f"  ⚠️ Firecrawl {article['url'][:60]}: {err}")

        # Embedding source: prefer body, fallback to title+summary
        embed_text = body or f"{title}. {summary}" if summary else title
        if not embed_text.strip():
            skipped += 1
            continue

        try:
            resp = emb_client.embeddings.create(input=embed_text[:8000], model=EMBED_MODEL)
            embedding = resp.data[0].embedding
        except Exception as e:
            log(f"  ⚠️ Embedding failed for {title[:50]}: {e}")
            embedding = []

        # Extract claims
        claims_texts, claims_confs = extract_claims(body or f"{title}. {summary}", emb_client, claim_count)
        
        # Create node with claims
        with neo4j_driver.session() as session:
            session.run("""
                MERGE (n:NewsArticle {uid: $uid})
                SET n.title = $title,
                    n.summary = $summary,
                    n.body = $body,
                    n.url = $url,
                    n.source = $source,
                    n.category = $category,
                    n.published_at = datetime($published),
                    n.fetched_at = datetime(),
                    n.embedding = $emb,
                    n.claims = $claims,
                    n.claim_count = $cc,
                    n.claim_confidences = $confs
            """, uid=uid, title=title, summary=summary,
                 body=body, url=article["url"], source=article["source"],
                 category=article["category"],
                 published=_parse_date(article["published_at"]),
                 emb=embedding, claims=claims_texts, cc=len(claims_texts), confs=claims_confs)
        count += 1

    return count, skipped, fc_success, fc_fail


def create_index(neo4j_driver):
    with neo4j_driver.session() as session:
        try:
            session.run("""
                CREATE VECTOR INDEX news_embedding_index IF NOT EXISTS
                FOR (n:NewsArticle) ON (n.embedding)
                OPTIONS {indexConfig: {
                    `vector.dimensions`: 768,
                    `vector.similarity_function`: "cosine"
                }}
            """)
        except Exception as e:
            log(f"  ⚠️ Index: {e}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-articles", type=int, default=6)
    p.add_argument("--claims", type=int, default=3, help="Number of claims to extract per article (default: 3)")
    p.add_argument("--no-firecrawl", action="store_true", help="Skip Firecrawl (headlines only)")
    args = p.parse_args()

    use_fc = not args.no_firecrawl
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    log(f"📰 News Ingester — {now}")
    if use_fc:
        log(f"  🔥 Firecrawl: enabled (free tier: 500/mo, using ~{args.max_articles}/day = ~{args.max_articles*30}/mo)")

    emb = OpenAI(api_key="ollama", base_url=EMBED_BASE)
    neo4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    create_index(neo4j)

    log("  Fetching news...")
    articles = fetch_news(args.max_articles)
    log(f"  Fetched {len(articles)} articles")

    if not articles:
        log("  ❌ No articles fetched")
        neo4j.close()
        return

    log("  Ingesting to Neo4j..." + (" (with Firecrawl full text)" if use_fc else " (headlines only)"))
    added, skipped, fc_ok, fc_fail = ingest_articles(articles, emb, neo4j, use_firecrawl=use_fc, claim_count=args.claims)

    log(f"\n  ✅ Added: {added} new articles")
    log(f"  ⏭️  Skipped (duplicates): {skipped}")
    if use_fc:
        log(f"  🔥 Firecrawl: {fc_ok} OK, {fc_fail} failed (total api calls this run: {fc_ok + fc_fail})")

    with neo4j.session() as session:
        total = session.run("MATCH (n:NewsArticle) RETURN count(n) AS c").single()["c"]
        log(f"  📊 Total NewsArticle nodes: {total}")

    neo4j.close()
    log("  Done")


if __name__ == "__main__":
    main()