"""Family Office Intelligence Pipeline — Demo Script.

Run from a fresh checkout to see the full system in action:
    python demo.py

Outputs:
  - Dataset summary (59 entities, quality metrics)
  - 6 RAG queries with results
  - Guardrail demonstrations
  - Export to data/demo_export.csv
"""
from __future__ import annotations

import csv
import io
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent
os.chdir(str(_project_root))
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

os.environ.setdefault("FO_DATA_DIR", "data")
os.environ.setdefault("ENABLE_SEC_ENRICHMENT", "false")
os.environ.setdefault("ENABLE_WEB_ENRICHMENT", "false")
os.environ.setdefault("REQUEST_DELAY_SEC", "0.0")

from config.settings import settings  # noqa: E402
from models.sfo import ContactConfidence  # noqa: E402
from pipeline.loader import SeedDataLoader  # noqa: E402
from rag.engine import MicroRAGEngine  # noqa: E402

DIVIDER = "=" * 70

def main():
    print(DIVIDER)
    print("  FAMILY OFFICE INTELLIGENCE PIPELINE — DEMO")
    print(DIVIDER)

    # ── Step 1: Load data ──────────────────────────────────────────────
    print("\n[1/4] Loading dataset...")
    loader = SeedDataLoader(settings.resolved_data_dir)
    enriched_path = loader.data_dir / "sfo_enriched.json"
    seed_path = loader.data_dir / "sfo_seed.json"

    if enriched_path.exists():
        collection = loader.load_json("sfo_enriched.json")
        source = "enriched"
    elif seed_path.exists():
        collection = loader.load_json("sfo_seed.json")
        source = "seed"
    else:
        print("  ERROR: No data files found. Run 'python cli.py pipeline' first.")
        sys.exit(1)

    entities = collection.entities
    total = len(entities)
    print(f"  Loaded {total} entities from data/{source}.json")

    # ── Step 2: Quality metrics ────────────────────────────────────────
    print("\n[2/4] Dataset quality metrics:")
    emails = sum(1 for e in entities if any(
        c.confidence in (ContactConfidence.VERIFIED_DIRECT, ContactConfidence.CATCH_ALL)
        for c in e.contacts
    ))
    principals = sum(1 for e in entities if e.principals)
    aum = sum(1 for e in entities if e.estimated_aum_usd is not None)
    sow = sum(1 for e in entities if e.source_of_wealth)
    websites = sum(1 for e in entities if e.website)
    years = sum(1 for e in entities if e.year_established)

    metrics = [
        ("Total entities", total, "100%"),
        ("With email", emails, f"{emails/total*100:.0f}%"),
        ("With principals", principals, f"{principals/total*100:.0f}%"),
        ("With AUM", aum, f"{aum/total*100:.0f}%"),
        ("With source of wealth", sow, f"{sow/total*100:.0f}%"),
        ("With website", websites, f"{websites/total*100:.0f}%"),
        ("With year established", years, f"{years/total*100:.0f}%"),
    ]
    for label, count, pct in metrics:
        print(f"  {label:<25} {count:>3} ({pct})")

    # ── Step 3: RAG queries ────────────────────────────────────────────
    print("\n[3/4] Running RAG queries...")
    rag = MicroRAGEngine()
    indexed = rag.index_collection(collection)
    print(f"  Indexed {indexed} entities into RAG.\n")

    queries = [
        ("Duquesne Family Office", "Entity-specific lookup"),
        ("New York family offices", "Location-based search"),
        ("AUM over $1 billion", "AUM filtering"),
        ("Pathstone principals", "Principal discovery"),
        ("source of wealth hedge fund", "SOW matching"),
        ("founded before 2000", "Temporal query"),
    ]

    for query_text, description in queries:
        print(f"  Q: {query_text} ({description})")
        result = rag.query(query_text=query_text, n_results=2)
        for r in result["results"]:
            aum_str = f"${r['aum']:,.0f}" if r["aum"] else "N/A"
            print(f"    [{r['similarity_score']:.3f}] {r['entity_name']} — AUM: {aum_str}, HQ: {r['hq']}")
        if result.get("guardrail_notes"):
            for note in result["guardrail_notes"][:1]:
                print(f"    {note}")
        print()

    # ── Step 4: Export ─────────────────────────────────────────────────
    print("[4/4] Exporting to CSV...")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "entity_name", "entity_type", "aum", "aum_confidence",
                      "hq_city", "principals", "email", "source_of_wealth"])
    for e in entities:
        best_email = e.best_principal_email()
        writer.writerow([
            e.id, e.entity_name, e.entity_type.value,
            e.estimated_aum_usd or "", e.aum_confidence.value,
            e.hq_city or "",
            "; ".join(p.full_name for p in e.principals),
            best_email.value if best_email else "",
            (e.source_of_wealth or "")[:100],
        ])
    out_path = _project_root / "data" / "demo_export.csv"
    out_path.write_text(buf.getvalue())
    print(f"  Exported {total} entities to {out_path}")

    print(f"\n{DIVIDER}")
    print("  DEMO COMPLETE — All systems operational.")
    print(DIVIDER)


if __name__ == "__main__":
    main()
