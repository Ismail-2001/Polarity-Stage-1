"""FastAPI backend for the Family Office Intelligence Pipeline & Micro-RAG.

Endpoints:
  GET  /                    Health check with detailed stats
  GET  /health              Deep health check (DB, RAG, data)
  POST /pipeline/run        Execute enrichment pipeline (async)
  GET  /pipeline/status/{id} Poll pipeline job status
  POST /query               Semantic RAG query
  GET  /entities            Paginated entity list
  GET  /entities/{id}       Single entity detail
  POST /index               (Re)index dataset into RAG
"""

from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.schemas import (
    EntityListItem,
    ErrorResponse,
    IndexResponse,
    PaginatedEntitiesResponse,
    PipelineJobStatus,
    PipelineRunResponse,
    QueryRequest,
    QueryResponse,
    StatusResponse,
)
from config.settings import utcnow
from models.sfo import AumConfidence, ContactConfidence, SFOCollection
from pipeline.loader import SeedDataLoader
from pipeline.orchestrator import PipelineOrchestrator
from rag.engine import MicroRAGEngine

app = FastAPI(
    title="Family Office Intelligence Pipeline & Micro-RAG",
    version="1.0.0",
    description="Commercial-grade SFO discovery, enrichment, validation, and semantic query.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Lazy globals
# ---------------------------------------------------------------------------

_rag: MicroRAGEngine | None = None
_loader: SeedDataLoader | None = None

# ---------------------------------------------------------------------------
# Pipeline job store (in-memory, async execution)
# ---------------------------------------------------------------------------

_jobs: dict[str, PipelineJobStatus] = {}
_jobs_lock = threading.Lock()


def get_rag() -> MicroRAGEngine:
    global _rag
    if _rag is None:
        _rag = MicroRAGEngine()
    return _rag


def get_loader() -> SeedDataLoader:
    global _loader
    if _loader is None:
        from config.settings import settings
        _loader = SeedDataLoader(settings.resolved_data_dir)
    return _loader


def _load_collection() -> SFOCollection:
    loader = get_loader()
    enriched = loader.data_dir / "sfo_enriched.json"
    seed = loader.data_dir / "sfo_seed.json"
    deprecated = loader.data_dir / "sfo_seed_DEPRECATED_famous_names.json"
    if enriched.exists():
        return loader.load_json("sfo_enriched.json")
    if seed.exists():
        return loader.load_json("sfo_seed.json")
    if deprecated.exists():
        return loader.load_json("sfo_seed_DEPRECATED_famous_names.json")
    return SFOCollection()


def _coll_stats(collection: SFOCollection) -> dict:
    entities = collection.entities
    unresolved = sum(
        1 for e in entities for c in e.contacts if c.confidence == ContactConfidence.UNRESOLVED
    )
    verified = sum(
        1 for e in entities for c in e.contacts if c.confidence == ContactConfidence.VERIFIED_DIRECT
    )
    return {
        "total_seed_records": len(entities),
        "unresolved_contacts": unresolved,
        "verified_contacts": verified,
    }


def _run_pipeline_job(job_id: str) -> None:
    """Background thread: run pipeline + index RAG, update job status."""
    with _jobs_lock:
        _jobs[job_id].status = "running"
        _jobs[job_id].started_at = utcnow().isoformat()

    result = None
    try:
        loader = get_loader()
        orchestrator = PipelineOrchestrator(loader)
        result = orchestrator.run()

        with _jobs_lock:
            job = _jobs[job_id]
            job.status = result.status.value
            job.total_records = result.total_records
            job.succeeded = result.succeeded
            job.failed = result.failed
            job.unresolved_contacts = result.unresolved_contacts
            job.steps = [s.dict() for s in result.steps]

        try:
            collection = _load_collection()
            rag = get_rag()
            indexed = rag.index_collection(collection)
            with _jobs_lock:
                _jobs[job_id].indexed_entities = indexed
        except Exception:
            pass

        with _jobs_lock:
            _jobs[job_id].completed_at = utcnow().isoformat()
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id].status = "failed"
            _jobs[job_id].error = str(e)
            _jobs[job_id].completed_at = utcnow().isoformat()
            if result is not None:
                _jobs[job_id].steps = [s.dict() for s in result.steps]


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=str(exc),
            code="internal_error",
        ).dict(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/", response_model=StatusResponse)
async def root():
    """Health check with dataset statistics."""
    rag = get_rag()
    coll = _load_collection()
    stats = _coll_stats(coll)
    return StatusResponse(
        status="operational",
        indexed_entities=rag.count(),
        dataset_loaded=rag.count() > 0,
        **stats,
    )


@app.get("/health")
async def health_check():
    """Deep health check: verifies data dir, RAG engine, and API key config."""
    from config.settings import settings
    checks = {}

    # Data directory
    data_dir = settings.resolved_data_dir
    checks["data_dir"] = {"status": "ok" if data_dir.exists() else "error", "path": str(data_dir)}

    # Seed file
    seed_path = data_dir / "sfo_seed.json"
    checks["seed_file"] = {"status": "ok" if seed_path.exists() else "missing"}

    # Enriched file
    enriched_path = data_dir / "sfo_enriched.json"
    checks["enriched_file"] = {"status": "ok" if enriched_path.exists() else "missing"}

    # RAG engine
    rag = get_rag()
    checks["rag_engine"] = {"status": "ok", "indexed_entities": rag.count()}

    # API keys
    checks["api_keys"] = {
        "serper": "configured" if settings.serper_api_key else "missing",
        "hunter": "configured" if settings.hunter_api_key else "missing",
    }

    all_ok = all(c.get("status") == "ok" for c in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}


@app.get("/status", response_model=StatusResponse)
async def status():
    """System status endpoint."""
    return await root()


@app.post("/pipeline/run", response_model=PipelineRunResponse)
async def run_pipeline():
    """Execute the full enrichment pipeline (async — returns job ID immediately)."""
    job_id = f"JOB-{utcnow().strftime('%Y%m%d-%H%M%S')}"
    job = PipelineJobStatus(job_id=job_id, status="pending")

    with _jobs_lock:
        _jobs[job_id] = job

    thread = threading.Thread(target=_run_pipeline_job, args=(job_id,), daemon=True)
    thread.start()

    return PipelineRunResponse(
        pipeline_id=job_id,
        status="pending",
        message="Pipeline started. Poll /pipeline/status/{job_id} for progress.",
    )


@app.get("/pipeline/status/{job_id}", response_model=PipelineJobStatus)
async def pipeline_status(job_id: str):
    """Poll pipeline job status."""
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@app.post("/query", response_model=QueryResponse)
async def query_rag(request: QueryRequest):
    """Semantic query against the SFO knowledge base with guardrails."""
    rag = get_rag()
    if rag.count() == 0:
        return QueryResponse(
            query=request.query,
            result_count=0,
            results=[],
            guardrail_notes=["No SFO records indexed. Run the pipeline first or load seed data."],
        )
    min_conf = None
    if request.min_confidence == "verified_direct":
        min_conf = AumConfidence.CONFIRMED
    result = rag.query(
        query_text=request.query,
        n_results=request.n_results or 5,
        min_confidence=min_conf,
    )
    return QueryResponse(**result)


@app.get("/entities", response_model=PaginatedEntitiesResponse)
async def list_entities(
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    sort_by: str = Query("entity_name", regex="^(entity_name|aum|enrichment_status)$"),
    order: str = Query("asc", regex="^(asc|desc)$"),
):
    """Paginated entity list with sorting."""
    rag = get_rag()
    raw = rag.list_entities(limit=limit, offset=offset)

    items = []
    for entry in raw:
        m = entry.get("metadata", {})
        items.append(
            EntityListItem(
                id=entry["id"],
                entity_name=m.get("entity_name", ""),
                entity_type=m.get("entity_type", ""),
                enrichment_status=m.get("enrichment_status", ""),
                aum_confidence=m.get("aum_confidence", ""),
                principal_count=m.get("principal_count", 0),
                verified_contacts=m.get("verified_contact_count", 0),
                has_unresolved=m.get("has_unresolved_contact", False),
            )
        )

    # Sort
    reverse = order == "desc"
    if sort_by == "aum":
        items.sort(key=lambda x: x.aum_confidence, reverse=reverse)
    elif sort_by == "enrichment_status":
        items.sort(key=lambda x: x.enrichment_status, reverse=reverse)
    else:
        items.sort(key=lambda x: x.entity_name.lower(), reverse=reverse)

    return PaginatedEntitiesResponse(
        total=rag.count(),
        limit=limit,
        offset=offset,
        count=len(items),
        results=items,
    )


@app.get("/entities/{entity_id}")
async def get_entity(entity_id: str):
    """Get a single SFO entity by ID with full detail."""
    rag = get_rag()
    entity = rag.get_entity(entity_id)
    if entity is None:
        raise HTTPException(
            status_code=404,
            detail=ErrorResponse(
                error=f"Entity {entity_id} not found",
                code="not_found",
            ).dict(),
        )
    return entity


@app.post("/index", response_model=IndexResponse)
async def index_dataset():
    """(Re)index the current dataset into the RAG engine."""
    try:
        collection = _load_collection()
        rag = get_rag()
        count = rag.index_collection(collection)
        return IndexResponse(indexed_entities=count, status="ok")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
