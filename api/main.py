"""FastAPI backend for the Family Office Intelligence Pipeline & Micro-RAG.

Endpoints:
  GET  /                    Health check with detailed stats
  POST /pipeline/run        Execute enrichment pipeline
  POST /query               Semantic RAG query
  GET  /entities            Paginated entity list
  GET  /entities/{id}       Single entity detail
  POST /index               (Re)index dataset into RAG
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.schemas import (
    EntityListItem,
    ErrorResponse,
    IndexResponse,
    PaginatedEntitiesResponse,
    PipelineRunResponse,
    QueryRequest,
    QueryResponse,
    StatusResponse,
)
from models.sfo import ContactConfidence, SFOCollection
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


@app.get("/status", response_model=StatusResponse)
async def status():
    """System status endpoint."""
    return await root()


@app.post("/pipeline/run", response_model=PipelineRunResponse)
async def run_pipeline():
    """Execute the full enrichment pipeline over the seed dataset."""
    try:
        loader = get_loader()
        orchestrator = PipelineOrchestrator(loader)
        result = orchestrator.run()

        collection = _load_collection()
        rag = get_rag()
        indexed = rag.index_collection(collection)

        return PipelineRunResponse(
            pipeline_id=result.pipeline_id,
            status=result.status.value,
            total_records=result.total_records,
            succeeded=result.succeeded,
            failed=result.failed,
            unresolved_contacts=result.unresolved_contacts,
            indexed_entities=indexed,
            steps=[s.dict() for s in result.steps],
            error=result.error,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        min_conf = ContactConfidence.VERIFIED_DIRECT
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
