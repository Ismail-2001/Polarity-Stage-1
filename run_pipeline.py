"""Standalone pipeline runner — enriches the seed dataset and indexes into ChromaDB.

Usage:
    python run_pipeline.py
"""

from config.settings import settings
from pipeline.loader import SeedDataLoader
from pipeline.orchestrator import PipelineOrchestrator
from rag.engine import MicroRAGEngine


def main():
    loader = SeedDataLoader(settings.resolved_data_dir)
    orchestrator = PipelineOrchestrator(loader)
    result = orchestrator.run()

    print(f"\n{'='*60}")
    print(f"Pipeline: {result.pipeline_id}")
    print(f"Status:   {result.status.value}")
    print(f"Total:    {result.total_records}")
    print(f"OK:       {result.succeeded}")
    print(f"Failed:   {result.failed}")
    print(f"Unresolved contacts: {result.unresolved_contacts}")
    print(f"{'='*60}\n")

    for step in result.steps:
        tag = "✅" if step.status.value == "completed" else "⚠️" if step.status.value == "partial" else "❌"
        print(f"  {tag} {step.step_name} ({step.records_processed} records)")

    # Index into RAG
    print("\nIndexing into ChromaDB Micro-RAG...")
    collection = loader.load_json("sfo_enriched.json")
    rag = MicroRAGEngine()
    count = rag.index_collection(collection)
    print(f"Indexed {count} entities. RAG ready.\n")


if __name__ == "__main__":
    main()
