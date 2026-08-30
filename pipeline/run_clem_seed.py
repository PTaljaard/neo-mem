#!/usr/bin/env python3
"""Execute (or dry-run) the Clem Sunter scenario lineage seed against D7 Neo4j.

Usage:
  python3 run_clem_seed.py plan     # dry-run: report what would be created (counts + intended lineage)
  python3 run_clem_seed.py run      # execute the seed (idempotent MERGE)
  python3 run_clem_seed.py verify   # verify post-seed state

Credentials from pipeline_config (bolt://192.168.1.20:7687, user neo4j).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pipeline_config import NEO4J_URI, NEO4J_USER, NEO4J_PASS, NEO4J_DATABASE  # noqa: E402

from neo4j import GraphDatabase  # noqa: E402

CYPHER_FILE = Path(__file__).resolve().parent / "seed_clem_scenarios.cypher"
SUPERSEDES_FILE = Path(__file__).resolve().parent / "seed_clem_supersedes.cypher"


def driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))


def _strip_comments_and_split(script):
    """Properly strip // and /* */ comments (line-aware), then split on ';'.
    Returns a list of non-empty Cypher statements (comments removed)."""
    lines = script.split("\n")
    out = []
    in_block = False
    for ln in lines:
        # handle block comments that may span lines
        if in_block:
            if "*/" in ln:
                ln = ln.split("*/", 1)[1]
                in_block = False
            else:
                continue
        # strip inline // comment (respect: not inside quotes — our seed has none in strings)
        if "//" in ln:
            ln = ln.split("//", 1)[0]
        # handle a block comment opening on this line
        if "/*" in ln:
            pre, _, rest = ln.partition("/*")
            ln = pre
            if "*/" in rest:
                ln += rest.split("*/", 1)[1]
            else:
                in_block = True
        out.append(ln)
    clean = "\n".join(out)
    stmts = [s.strip() for s in clean.split(";") if s.strip()]
    return stmts


def run_cypher(tx):
    """Execute each statement of the seed file separately (driver = 1 stmt per query)."""
    script = CYPHER_FILE.read_text()
    for stmt in _strip_comments_and_split(script):
        tx.run(stmt)


def plan():
    # Pre-flight: read intended lineage from the script comments; verify connectivity
    # and report current state before any change.
    d = driver()
    with d.session(database=NEO4J_DATABASE) as s:
        curt = s.run("MATCH (s:Scenario) WHERE s.author_uid='clem-sunter' "
                     "RETURN s.scenario_key, s.version, s.name, s.status ORDER BY s.scenario_key, s.version")
        existing = list(curt)
        print("EXISTING Clem Sunter :Scenario nodes (pre-seed):")
        for r in existing:
            rec = r.data()
            print(f"  {rec['scenario_key']} v{rec['version']} | {rec['name']} | status={rec['status']}")
        if not existing:
            print("  (none — clean slate)")
        pers = s.run("MATCH (p:Person {name:'Clem Sunter'}) RETURN p.name, p.role, p.author_uid").data()
        print("Person node:", pers)
        sups = s.run("MATCH (a)-[:SUPERSEDES]->(b) WHERE a.author_uid='clem-sunter' "
                     "RETURN a.scenario_key AS k, a.version AS av, b.version AS bv").data()
        print("Existing SUPERSEDES (clem):", sups)
    d.close()
    print("\nPLAN: seed will create lineage nodes per pipeline/seed_clem_scenarios.cypher")
    print("  high_road  v1(1987) --SUPERSEDES--> v2(1996) --SUPERSEDES--> v3(People's Economy, active)")
    print("  low_road   v1(1987) --SUPERSEDES--> v2(Cautionary Tale, active)")
    print("  wasteland  v1 (Failed State, active)  |  friendly_planet v1 (active)  |  gilded_cage v1 (active)")
    print("  Person Clem Sunter -[AUTHORED]-> each scenario node")


def execute():
    d = driver()
    with d.session(database=NEO4J_DATABASE) as s:
        print("Executing scenario seed (idempotent MERGE)...")
        s.execute_write(run_cypher)
        print("Scenario seed executed.")
        print("Executing SUPERSEDES edges seed...")
        for stmt in _strip_comments_and_split(SUPERSEDES_FILE.read_text()):
            s.run(stmt)
        print("SUPERSEDES seed executed.")
    d.close()


def verify():
    d = driver()
    with d.session(database=NEO4J_DATABASE) as s:
        nodes = s.run("MATCH (s:Scenario) WHERE s.author_uid='clem-sunter' "
                      "RETURN s.scenario_key AS key, s.version AS v, s.name AS name, "
                      "s.status AS status, s.probability_current AS p, s.valid_from AS vf "
                      "ORDER BY s.scenario_key, s.version").data()
        print("=== SCENARIO NODES (author=clem-sunter) ===")
        for r in nodes:
            print(f"  {r['key']} v{r['v']} | {r['name']} | {r['status']} | p={r['p']} | since {r['vf']}")

        edges = s.run("MATCH (a:Scenario)-[su:SUPERSEDES]->(b:Scenario) WHERE a.author_uid='clem-sunter' "
                      "RETURN a.scenario_key AS key, a.version AS av, a.name AS an, "
                      "b.version AS bv, b.name AS bn ORDER BY a.scenario_key, a.version").data()
        print("=== SUPERSEDES CHAINS (author=clem-sunter) ===")
        for r in edges:
            print(f"  {r['key']}: v{r['av']} {r['an']}  ---SUPERSEDES-->  v{r['bv']} {r['bn']}")

        auth = s.run("MATCH (p:Person {name:'Clem Sunter'})-[a:AUTHORED]->(sc:Scenario) "
                     "RETURN count(a) AS authored, count(DISTINCT sc) AS nodes").data()
        print("=== AUTHOR ATTRIBUTION ===")
        print("  AUTHORED edges:", auth[0]['authored'], "| distinct Scenario nodes:", auth[0]['nodes'])

        act = s.run("MATCH (s:Scenario) WHERE s.author_uid='clem-sunter' AND s.status='active' "
                    "RETURN count(s) AS active").data()
        sup = s.run("MATCH (s:Scenario) WHERE s.author_uid='clem-sunter' AND s.status='superseded' "
                    "RETURN count(s) AS superseded").data()
        print(f"  active={act[0]['active']}  superseded={sup[0]['superseded']}")
    d.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if cmd == "plan":
        plan()
    elif cmd == "run":
        execute()
    elif cmd == "verify":
        verify()
    else:
        print("usage: run_clem_seed.py [plan|run|verify]")
        sys.exit(2)
