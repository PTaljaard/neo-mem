#!/usr/bin/env python3
"""
Model Comparison: Run 10 identical chunks through gemma4, nemotron, deepseek.
Reports speed, event count, and quality comparison.
No Neo4j writes — just output for analysis.
"""
import sys, time, json, re, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, OLLAMA_BASE, CHAT_MODEL
from neo4j import GraphDatabase
from openai import OpenAI

log = print

# ── EV prompt (same as extract_events.py) ─────────────────────────────
EV_PROMPT = """Analyze the following passage and extract events with their participating entities.

An EVENT is a single sentence describing something that happens. Each event has ENTITIES that participate in it.

Return ONLY valid JSON. No markdown, no code fences.
{{"events": [
  {{"event": "sentence describing the event",
   "entities": ["entity1", "entity2"]}}
]}}

Passage:
{text}"""


def repair_json(raw):
    raw = re.sub(r'```(?:json)?\s*', '', raw).strip()
    raw = re.sub(r'\s*```', '', raw)
    s, e = raw.find('{'), raw.rfind('}')
    if s == -1 or e == -1:
        return {}
    raw = raw[s:e+1]
    try:
        return json.loads(raw)
    except:
        raw = re.sub(r',\s*}', '}', raw)
        raw = re.sub(r',\s*]', ']', raw)
        try:
            return json.loads(raw)
        except:
            return {}


def run_model(model_config, chunk_text, chunk_id):
    """Run EV extraction with one model. Returns (events, elapsed_seconds, response_text)."""
    llm = OpenAI(base_url=model_config["base_url"], api_key=model_config["api_key"])
    prompt = EV_PROMPT.format(text=chunk_text[:4000])
    start = time.time()
    try:
        resp = llm.chat.completions.create(
            model=model_config["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=512
        )
        elapsed = time.time() - start
        raw = resp.choices[0].message.content
        data = repair_json(raw)
        events = data.get("events", [])
        valid = [e for e in events if isinstance(e, dict) and "event" in e and e["event"].strip()]
        return valid, elapsed, raw
    except Exception as e:
        return [], time.time() - start, f"ERROR: {e}"


def main():
    p = argparse.ArgumentParser(description="Compare event extraction across 3 models")
    p.add_argument("--limit", type=int, default=10, help="Chunks to test per model")
    args = p.parse_args()

    # Model configs
    models = {
        "gemma4": {
            "model": "gemma4:e4b-it-qat",
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",
        },
        "nemotron": {
            "model": "openrouter/nvidia/nemotron-3.5-lightning:free",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",  # Will be read from env
        },
        "deepseek": {
            "model": "deepseek/deepseek-v4-flash",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "",  # Will be read from env
        },
    }

    # Try to read OpenRouter API key
    import os
    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    # Also try reading from config
    if not or_key:
        try:
            r = subprocess.run(["hermes", "config", "get", "providers.openrouter.key"],
                              capture_output=True, text=True, timeout=5)
            or_key = r.stdout.strip()
        except:
            pass
    if not or_key:
        # Check if the key is in the .hermes/.env file
        env_path = Path.home() / ".hermes" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "OPENROUTER_API_KEY" in line:
                    or_key = line.split("=", 1)[1].strip()
                    break
    if or_key:
        models["nemotron"]["api_key"] = or_key
        models["deepseek"]["api_key"] = or_key

    # Fetch 10 chunks from DIAL-KG paper
    n4j = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
    with n4j.session() as s:
        r = s.run("""
            MATCH (c:Chunk)
            WHERE c.doc_id = 'dial-kg-2603.20059' AND c.uid IS NOT NULL
            RETURN c.uid, c.text
            ORDER BY c.chunk_index
            LIMIT $limit
        """, limit=args.limit)
        chunks = [(row["c.uid"], row["c.text"]) for row in r]
    n4j.close()

    if not chunks:
        log("❌ No chunks found for DIAL-KG paper")
        sys.exit(1)

    log(f"\n{'='*70}")
    log(f"MODEL COMPARISON: {len(chunks)} chunks × 3 models")
    log(f"{'='*70}\n")

    results = {}
    for model_name, config in models.items():
        if not config["api_key"]:
            log(f"  ⚠️ {model_name}: No API key — skipping")
            continue

        log(f"\n🧪 Testing {model_name}...")
        total_events = 0
        total_time = 0
        model_results = []

        for uid, text in chunks:
            events, elapsed, raw = run_model(config, text, uid)
            total_events += len(events)
            total_time += elapsed
            model_results.append({
                "uid": uid,
                "events": events,
                "elapsed": elapsed,
                "raw_preview": raw[:100]
            })

            emoji = "✅" if events else "⬜"
            log(f"  {emoji} {uid[:30]:30s} {len(events):2d} events  {elapsed:5.1f}s")

        avg_time = total_time / len(chunks)
        log(f"\n  📊 {model_name}: {total_events} events from {len(chunks)} chunks, "
            f"{avg_time:.1f}s avg, {total_time:.0f}s total")

        results[model_name] = {
            "total_events": total_events,
            "total_time": total_time,
            "avg_time": avg_time,
            "details": model_results,
        }

    # Comparison table
    print(f"\n{'='*70}")
    print(f"COMPARISON TABLE")
    print(f"{'='*70}")
    print(f"  {'Model':15s} {'Events':>8s} {'Avg Time':>10s} {'Total':>8s} {'Events/Chunk':>14s}")
    print(f"  {'-'*15} {'-'*8} {'-'*10} {'-'*8} {'-'*14}")
    for name, r in results.items():
        e_per_c = r["total_events"] / max(len(chunks), 1)
        print(f"  {name:15s} {r['total_events']:>8d} {r['avg_time']:>7.1f}s {r['total_time']:>6.0f}s {e_per_c:>10.1f}")

    # Quality comparison: sample events per model for same chunk
    print(f"\n{'='*70}")
    print(f"QUALITY SAMPLE: First chunk events per model")
    print(f"{'='*70}")
    for name, r in results.items():
        if r["details"]:
            first = r["details"][0]
            print(f"\n  {name.upper()}:")
            for ev in first["events"][:5]:
                print(f"    • {ev['event'][:80]}")
                if ev.get("entities"):
                    print(f"      entities: {ev['entities'][:5]}")
            print(f"    ({first['elapsed']:.1f}s, {len(first['events'])} events)")

    n4j.close()


if __name__ == "__main__":
    import subprocess  # for API key lookup
    main()