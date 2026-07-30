"""FastAPI request/response schemas — Pydantic V1 compatible."""


from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    status: str
    indexed_entities: int = 0
    dataset_loaded: bool = False
    version: str = "1.0.0"
    total_seed_records: int = 0
    unresolved_contacts: int = 0
    verified_contacts: int = 0


class PipelineRunResponse(BaseModel):
    pipeline_id: str
    status: str
    message: str = ""


class PipelineJobStatus(BaseModel):
    job_id: str
    status: str = "pending"
    total_records: int = 0
    succeeded: int = 0
    failed: int = 0
    unresolved_contacts: int = 0
    indexed_entities: int = 0
    steps: list[dict] = Field(default_factory=list)
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None

    class Config:
        # Allow datetime objects to be coerced to strings
        json_encoders = {}


class QueryRequest(BaseModel):
    query: str
    n_results: int = Field(default=5, ge=1, le=20)
    min_confidence: str | None = Field(
        default=None,
        description="Filter: 'verified_direct' excludes entities with unresolved AUM",
    )


class QueryResultItem(BaseModel):
    id: str
    entity_name: str
    entity_type: str
    family_name: str = ""
    source_of_wealth: str = ""
    aum: float = 0.0
    aum_confidence: str = ""
    hq: str = ""
    principal_count: int = 0
    verified_contact_count: int = 0
    has_unresolved_contact: bool = False
    enrichment_status: str = ""
    raw_document: str = ""
    similarity_score: float = 0.0
    unresolved_warning: str = ""


class QueryResponse(BaseModel):
    query: str
    result_count: int = 0
    results: list[QueryResultItem] = Field(default_factory=list)
    guardrail_notes: list[str] = Field(default_factory=list)


class IndexResponse(BaseModel):
    indexed_entities: int
    status: str = "ok"


class EntityListItem(BaseModel):
    id: str
    entity_name: str
    entity_type: str
    enrichment_status: str
    aum_confidence: str
    principal_count: int
    verified_contacts: int
    has_unresolved: bool


class PaginatedEntitiesResponse(BaseModel):
    total: int
    limit: int
    offset: int
    count: int
    results: list[EntityListItem]


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    code: str = "internal_error"
