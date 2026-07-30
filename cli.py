#!/usr/bin/env python3
"""Unified CLI for the Family Office Intelligence Pipeline & Micro-RAG.

Usage:
    python cli.py pipeline              # Run enrichment pipeline
    python cli.py query  "text"         # Semantic query against RAG
    python cli.py serve                 # Start FastAPI server
    python cli.py ui                    # Start Streamlit UI
    python cli.py export  --format csv  # Export enriched data
    python cli.py validate              # Validate dataset integrity
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root on path
_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from config.settings import settings


def cmd_pipeline(args: argparse.Namespace) -> None:
    """Run the enrichment pipeline."""
    from pipeline.loader import SeedDataLoader
    from pipeline.orchestrator import PipelineOrchestrator
    from rag.engine import MicroRAGEngine

    loader = SeedDataLoader(settings.resolved_data_dir)
    orchestrator = PipelineOrchestrator(loader)
    result = orchestrator.run()

    print(f"\n{'=' * 60}")
    print(f"Pipeline: {result.pipeline_id}")
    print(f"Status:   {result.status.value}")
    print(f"Total:    {result.total_records}")
    print(f"OK:       {result.succeeded}")
    print(f"Failed:   {result.failed}")
    print(f"Unresolved: {result.unresolved_contacts}")
    print(f"{'=' * 60}\n")

    for step in result.steps:
        icon = {"completed": "\u2705", "partial": "\u26a0\ufe0f", "failed": "\u274c", "pending": "\u23f3"}
        print(f"  {icon.get(step.status.value, '?')} {step.step_name} ({step.records_processed} records)")

    # Index into RAG
    print("\nIndexing into RAG...")
    collection = loader.load_json("sfo_enriched.json")
    rag = MicroRAGEngine()
    count = rag.index_collection(collection)
    print(f"Indexed {count} entities. RAG ready.\n")


def cmd_query(args: argparse.Namespace) -> None:
    """Query the RAG system."""
    from rag.engine import MicroRAGEngine
    from pipeline.loader import SeedDataLoader

    rag = MicroRAGEngine()
    if rag.count() == 0:
        # Attempt to load from enriched JSON
        loader = SeedDataLoader(settings.resolved_data_dir)
        enriched = loader.data_dir / "sfo_enriched.json"
        seed = loader.data_dir / "sfo_seed.json"
        source = enriched if enriched.exists() else (seed if seed.exists() else None)
        if source:
            print(f"Loading data from {source.name} into RAG...")
            collection = loader.load_json(source.name)
            count = rag.index_collection(collection)
            print(f"Indexed {count} entities.\n")
        else:
            print("No data found. Run `python cli.py pipeline` first.")
            sys.exit(1)

    result = rag.query(query_text=args.text, n_results=args.n_results)
    print(f"\nQuery: {result['query']}")
    print(f"Results: {result['result_count']}\n")

    for r in result["results"]:
        print(f"  [{r['similarity_score']:.3f}] {r['entity_name']} ({r['entity_type']})")
        print(f"        Family: {r['family_name'] or 'N/A'} | AUM: ${r['aum']:,.0f} | {r['aum_confidence']}")
        print(f"        HQ: {r['hq']} | Principals: {r['principal_count']}")
        if r.get("unresolved_warning"):
            print(f"        \u26a0 {r['unresolved_warning']}")
        print()

    for note in result.get("guardrail_notes", []):
        print(f"[GUARDRAIL] {note}")


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the FastAPI server."""
    import uvicorn

    host = args.host or "0.0.0.0"
    port = args.port or 8000
    print(f"Starting FO Intelligence API on {host}:{port}...")
    uvicorn.run("api.main:app", host=host, port=port, reload=args.reload)


def cmd_ui(args: argparse.Namespace) -> None:
    """Start the Streamlit UI."""
    import subprocess

    port = args.port or 8501
    ui_path = str(_project_root / "ui" / "app.py")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", ui_path, f"--server.port={port}"],
        check=False,
    )


def cmd_export(args: argparse.Namespace) -> None:
    """Export enriched data in various formats."""
    import csv
    import io

    from pipeline.loader import SeedDataLoader

    loader = SeedDataLoader(settings.resolved_data_dir)
    enriched_path = loader.data_dir / "sfo_enriched.json"
    seed_path = loader.data_dir / "sfo_seed.json"
    source = enriched_path if enriched_path.exists() else seed_path

    if not source.exists():
        print("No data found. Run the pipeline first.")
        sys.exit(1)

    collection = loader.load_json(source.name)
    entities = collection.entities

    if args.format == "json":
        output = json.dumps([e.to_record() for e in entities], indent=2, default=str)
        out_path = _project_root / "data" / "export.json"
        out_path.write_text(output)
        print(f"Exported {len(entities)} entities to {out_path}")

    elif args.format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "id", "entity_name", "entity_type", "family_name", "source_of_wealth",
            "estimated_aum_usd", "aum_confidence", "hq_city", "hq_country",
            "principals", "contacts", "enrichment_status",
        ])
        for e in entities:
            writer.writerow([
                e.id, e.entity_name, e.entity_type.value, e.family_name or "",
                e.source_of_wealth or "", e.estimated_aum_usd or "",
                e.aum_confidence.value, e.hq_city or "", e.hq_country or "",
                "; ".join(p.full_name for p in e.principals),
                "; ".join(f"{c.value} [{c.confidence.value}]" for c in e.contacts),
                e.enrichment_status.value,
            ])
        out_path = _project_root / "data" / "export.csv"
        out_path.write_text(buf.getvalue())
        print(f"Exported {len(entities)} entities to {out_path}")

    else:
        print(f"Unsupported format: {args.format}")


def cmd_discover(args: argparse.Namespace) -> None:
    """Discover SFO candidates from one or more sources."""
    from pipeline.loader import SeedDataLoader

    if args.source == "multi":
        from enrichment.discovery_wikipedia import run_multi_source_discovery
        print(f"Running multi-source discovery (up to {args.max_candidates} candidates)...")
        candidates = run_multi_source_discovery(max_candidates=args.max_candidates)
    elif args.source == "wikipedia":
        from enrichment.discovery_wikipedia import run_wikipedia_discovery
        print(f"Discovering from Wikipedia list (up to {args.max_candidates})...")
        candidates = run_wikipedia_discovery(max_candidates=args.max_candidates)
    else:
        from enrichment.discovery import run_discovery
        print(f"Discovering from SEC EFTS (up to {args.max_candidates})...")
        candidates = run_discovery(max_candidates=args.max_candidates)

    print(f"Found {len(candidates)} candidates. Saving to seed file...")
    loader = SeedDataLoader(settings.resolved_data_dir)
    from models.sfo import SFOCollection, SFOEntity
    collection = SFOCollection()
    for item in candidates:
        collection.add(SFOEntity(**item))
    path = loader.save_json(collection, args.output)
    print(f"Saved to {path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate dataset integrity."""
    from pipeline.loader import SeedDataLoader
    from models.sfo import AumConfidence

    loader = SeedDataLoader(settings.resolved_data_dir)
    enriched_path = loader.data_dir / "sfo_enriched.json"
    seed_path = loader.data_dir / "sfo_seed.json"
    source = enriched_path if enriched_path.exists() else seed_path

    if not source.exists():
        print("No data found.")
        sys.exit(1)

    collection = loader.load_json(source.name)
    errors = []

    for entity in collection.entities:
        eid = entity.id
        ename = entity.entity_name

        # 1. Entity name required
        if not entity.entity_name.strip():
            errors.append(f"[FAIL] {eid}: empty entity_name")

        # 2. Entity type must be SFO (in this dataset)
        if entity.entity_type.value != "SFO":
            errors.append(f"[WARN] {eid} {ename}: type is {entity.entity_type.value}, not SFO")

        # 3. No fabricated contacts
        for c in entity.contacts:
            if c.confidence.value == "Verified Direct Work Email":
                # Verify it's a real email (not Unresolved sentinel)
                if c.value == "Unresolved":
                    errors.append(f"[FAIL] {eid} {ename}: Verified contact but value='Unresolved'")
                # Verify not a generic inbox
                local = c.value.split("@")[0].lower().replace(".", "").replace("_", "").replace("-", "")
                if local in {"info", "contact", "investments", "support", "hello", "admin", "team", "enquiries", "office"}:
                    errors.append(f"[WARN] {eid} {ename}: Verified contact {c.value} appears to be generic")

            # 4. Unresolved contacts must have notes
            if c.confidence.value == "Unresolved" and not c.notes:
                errors.append(f"[INFO] {eid} {ename}: Unresolved contact without explanatory notes")

        # 5. LinkedIn validation
        for p in entity.principals:
            if p.linkedin_url:
                import re
                if not re.match(r"^https?:\/\/(www\.)?linkedin\.com\/in\/[a-zA-Z0-9_-]+\/?$", p.linkedin_url):
                    errors.append(f"[FAIL] {eid} {ename}: Invalid LinkedIn URL for {p.full_name}: {p.linkedin_url}")

        # 6. AUM confidence consistency
        if entity.aum_confidence.value == AumConfidence.CONFIRMED.value and not entity.estimated_aum_usd:
            errors.append(f"[FAIL] {eid} {ename}: Verified AUM confidence but no AUM value")

    print(f"\nValidation Report for: {source.name}")
    print(f"  Entities checked: {collection.count()}")
    print(f"  Issues found:     {len(errors)}\n")

    if errors:
        for err in errors:
            print(f"  {err}")
    else:
        print("  \u2705 All checks passed — dataset integrity confirmed.")

    return len(errors)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Family Office Intelligence Pipeline & Micro-RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # pipeline
    p = sub.add_parser("pipeline", help="Run enrichment pipeline")
    p.set_defaults(func=cmd_pipeline)

    # query
    p = sub.add_parser("query", help="Semantic query against RAG")
    p.add_argument("text", help="Query text")
    p.add_argument("-n", "--n-results", type=int, default=5, help="Number of results")
    p.set_defaults(func=cmd_query)

    # serve
    p = sub.add_parser("serve", help="Start FastAPI server")
    p.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    p.set_defaults(func=cmd_serve)

    # ui
    p = sub.add_parser("ui", help="Start Streamlit UI")
    p.add_argument("--port", type=int, default=8501, help="Port (default: 8501)")
    p.set_defaults(func=cmd_ui)

    # export
    p = sub.add_parser("export", help="Export enriched data")
    p.add_argument("-f", "--format", choices=["json", "csv"], default="json", help="Export format")
    p.set_defaults(func=cmd_export)

    # discover
    p = sub.add_parser("discover", help="Discover SFO candidates from public sources")
    p.add_argument("--max", type=int, default=50, dest="max_candidates", help="Max candidates")
    p.add_argument("-o", "--output", default="sfo_seed.json", help="Output filename")
    p.add_argument("-s", "--source", choices=["sec", "wikipedia", "multi"], default="sec",
                   help="Discovery source(s): sec, wikipedia, or multi (both, deduped)")
    p.set_defaults(func=cmd_discover)

    # validate
    p = sub.add_parser("validate", help="Validate dataset integrity")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
