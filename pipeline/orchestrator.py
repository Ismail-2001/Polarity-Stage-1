"""Pipeline orchestrator — runs the full discovery/enrichment/validation workflow over the seed dataset."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from audit import AuditLogger
from config.settings import settings
from enrichment.orchestrator import EnrichmentOrchestrator
from models.pipeline import ExecutionStep, PipelineResult, PipelineStatus
from models.sfo import SFOCollection
from pipeline.loader import SeedDataLoader


class PipelineOrchestrator:
    """Orchestrates batch enrichment of the SFO seed dataset."""

    def __init__(self, data_loader: SeedDataLoader):
        self.loader = data_loader
        self.enricher = EnrichmentOrchestrator()
        self._audit = AuditLogger("pipeline_main")
        self._result = PipelineResult()

    def run(self) -> PipelineResult:
        """Execute the full pipeline."""
        self._result.status = PipelineStatus.RUNNING
        self._result.started_at = datetime.now(timezone.utc)
        self._audit.log(f"Pipeline started (batch_size={settings.pipeline_batch_size})")

        # --- Step 1: Load seed data ---
        step_load = ExecutionStep(step_name="load_seed", status=PipelineStatus.RUNNING)
        try:
            seed_files = ["sfo_seed.json", "sfo_seed_DEPRECATED_famous_names.json", "sfo_enriched.json"]
            collection = SFOCollection()
            for fname in seed_files:
                path = self.loader.data_dir / fname
                if path.exists():
                    collection = self.loader.load_json(fname)
                    break
            else:
                raise FileNotFoundError(
                    f"No seed file found in {self.loader.data_dir}. "
                    f"Tried: {seed_files}"
                )
            self._result.total_records = collection.count()
            step_load.records_processed = collection.count()
            step_load.status = PipelineStatus.COMPLETED
            step_load.details = f"Loaded {collection.count()} seed entities"
            self._result.steps.append(step_load)
            self._audit.log(f"Loaded {collection.count()} seed entities")
        except Exception as e:
            step_load.status = PipelineStatus.FAILED
            step_load.errors.append(str(e))
            self._result.steps.append(step_load)
            self._result.status = PipelineStatus.FAILED
            self._result.error = str(e)
            return self._result

        # --- Step 2: Enrich ---
        step_enrich = ExecutionStep(step_name="enrich_entities", status=PipelineStatus.RUNNING)
        enriched = SFOCollection()
        batch = 0
        for i, entity in enumerate(collection.entities):
            try:
                self._audit.log(f"Enriching [{i+1}/{collection.count()}] {entity.entity_name}")
                self.enricher.enrich(entity)
                enriched.add(entity)
                self._result.succeeded += 1
                step_enrich.records_processed += 1
                # Rate limiting
                if (i + 1) % settings.pipeline_batch_size == 0:
                    batch += 1
                    self._audit.log(f"Batch {batch} complete, pausing...")
                    time.sleep(2)
            except Exception as e:
                self._result.failed += 1
                step_enrich.errors.append(f"{entity.entity_name}: {e}")
                # Still add partially-enriched entity
                enriched.add(entity)
                self._audit.log_failure(
                    "pipeline", error=str(e), entity=entity.entity_name,
                )
        step_enrich.status = PipelineStatus.COMPLETED if self._result.failed == 0 else PipelineStatus.PARTIAL
        step_enrich.ended_at = datetime.now(timezone.utc)
        self._result.steps.append(step_enrich)
        self._result.unresolved_contacts = enriched.unresolved_contacts_count()

        # --- Step 3: Persist ---
        step_save = ExecutionStep(step_name="persist_results", status=PipelineStatus.RUNNING)
        try:
            path = self.loader.save_json(enriched)
            step_save.records_processed = enriched.count()
            step_save.status = PipelineStatus.COMPLETED
            step_save.details = f"Saved to {path}"
            self._result.steps.append(step_save)
            self._audit.log(f"Results saved to {path}")
        except Exception as e:
            step_save.status = PipelineStatus.FAILED
            step_save.errors.append(str(e))
            self._result.steps.append(step_save)
            self._result.failed += 1  # Count as failed if persistence fails

        # --- Finalize ---
        self._result.completed_at = datetime.now(timezone.utc)
        if step_save.status == PipelineStatus.FAILED:
            self._result.status = PipelineStatus.PARTIAL
        else:
            self._result.status = (
                PipelineStatus.COMPLETED if self._result.failed == 0 else PipelineStatus.PARTIAL
            )
        self._audit.log_summary(
            total=self._result.total_records,
            succeeded=self._result.succeeded,
            failed=self._result.failed,
            unresolved=self._result.unresolved_contacts,
        )
        self._audit.log(f"Pipeline finished: {self._result.status.value}")
        return self._result
