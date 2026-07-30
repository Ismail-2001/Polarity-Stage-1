"""Pydantic models for SFO entities with strict validation and confidence tracking.

Uses Pydantic V1 API for Python 3.15-beta compatibility.
Supports "Unresolved" as a sentinel email value for honestly undocumented contacts.
"""

import hashlib
import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, root_validator, validator

from config.settings import utcnow

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ContactConfidence(str, Enum):
    VERIFIED_DIRECT = "Verified Direct Work Email"
    CATCH_ALL = "Catch-all / Generic Inbox"
    UNRESOLVED = "Unresolved"
    UNVERIFIED = "Unverified"


class AumConfidence(str, Enum):
    CONFIRMED = "Confirmed"
    ESTIMATED = "Estimated"
    UNRESOLVED = "Unresolved"
    UNKNOWN = "Unknown"
    UNDISCLOSED = "Undisclosed"


class EntityType(str, Enum):
    SFO = "SFO"
    MFO = "MFO"
    VC = "VC"
    UNKNOWN = "Unknown"


class EnrichmentStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# Source tracking
# ---------------------------------------------------------------------------

class EnrichmentSource(BaseModel):
    source_name: str
    url: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    field_extracted: str
    raw_snippet: str | None = None


# ---------------------------------------------------------------------------
# Contact
# ---------------------------------------------------------------------------

EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
GENERIC_MAILBOXES = {"info", "contact", "investments", "support", "hello", "admin", "team", "enquiries", "office"}
UNRESOLVED_SENTINEL = "Unresolved"


class ContactMethod(BaseModel):
    type: str = "email"
    value: str
    confidence: ContactConfidence = ContactConfidence.UNVERIFIED
    sources: list[EnrichmentSource] = Field(default_factory=list)
    notes: str | None = None

    @root_validator(pre=False)
    def validate_and_tag_email(cls, values):  # noqa: N805
        vtype = values.get("type")
        vval = values.get("value")
        confidence = values.get("confidence")

        if vtype == "email":
            # Allow the sentinel value for unresolved contacts
            if vval == UNRESOLVED_SENTINEL:
                if confidence != ContactConfidence.UNRESOLVED:
                    values["confidence"] = ContactConfidence.UNRESOLVED
                return values
            # Validate email format
            if not EMAIL_PATTERN.match(vval):
                raise ValueError(f"Invalid email format: {vval}")
            # Auto-downgrade generic inboxes to CATCH_ALL
            local = vval.split("@")[0].replace(".", "").replace("_", "").replace("-", "").lower()
            if local in GENERIC_MAILBOXES and confidence in (ContactConfidence.UNVERIFIED, ContactConfidence.VERIFIED_DIRECT):
                values["confidence"] = ContactConfidence.CATCH_ALL
        return values


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------

class Principal(BaseModel):
    full_name: str
    title: str | None = None
    linkedin_url: str | None = None
    notes: str | None = None
    sources: list[EnrichmentSource] = Field(default_factory=list)

    @validator("linkedin_url")
    def validate_linkedin(cls, v):  # noqa: N805
        if v is not None and v != UNRESOLVED_SENTINEL and not re.match(r"^https?:\/\/(www\.)?linkedin\.com\/in\/[a-zA-Z0-9_-]+\/?$", v):
            raise ValueError(
                f"Invalid LinkedIn profile URL: {v}. "
                "Must be a direct individual profile (linkedin.com/in/...)."
            )
        return v

    @validator("full_name")
    def name_not_empty(cls, v):  # noqa: N805
        if not v.strip():
            raise ValueError("full_name must not be empty")
        return v.strip()


# ---------------------------------------------------------------------------
# Signal (contextual intelligence signal)
# ---------------------------------------------------------------------------


class Signal(BaseModel):
    type: str
    description: str
    date: str
    source: str


# ---------------------------------------------------------------------------
# SFO Entity
# ---------------------------------------------------------------------------

SFO_ID_PREFIX = "SFO"


def _make_deterministic_id(entity_name: str) -> str:
    """Generate a deterministic ID from entity name (SHA256 hex, first 8 chars)."""
    digest = hashlib.sha256(entity_name.lower().strip().encode()).hexdigest()
    return f"{SFO_ID_PREFIX}-{digest[:8].upper()}"


class SFOEntity(BaseModel):
    id: str = Field(default="")
    entity_name: str
    entity_type: EntityType = EntityType.SFO
    also_known_as: str | None = None

    family_name: str | None = None
    source_of_wealth: str | None = None
    cik: str | None = None
    estimated_aum_usd: float | None = None
    aum_confidence: AumConfidence = AumConfidence.UNKNOWN
    year_established: int | None = None
    website: str | None = None
    hq_city: str | None = None
    hq_country: str | None = "United States"
    discovery_source: str | None = None
    inclusion_evidence: str | None = None

    principals: list[Principal] = Field(default_factory=list)
    contacts: list[ContactMethod] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)

    enrichment_status: EnrichmentStatus = EnrichmentStatus.PENDING
    last_enriched_at: datetime | None = None
    last_verified_at: datetime | None = None
    sources: list[EnrichmentSource] = Field(default_factory=list)
    audit_log: list[str] = Field(default_factory=list)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @validator("website")
    def validate_website(cls, v):  # noqa: N805
        if v is not None and not v.startswith("http"):
            raise ValueError(f"Website must start with http(s): {v}")
        return v

    @root_validator(pre=False)
    def require_entity_name(cls, values):  # noqa: N805
        name = values.get("entity_name")
        if not name or not str(name).strip():
            raise ValueError("entity_name is required")
        # Generate deterministic ID from entity name if not already set
        if not values.get("id"):
            values["id"] = _make_deterministic_id(name)
        return values

    def add_principal(self, principal: Principal) -> None:
        self.principals.append(principal)
        self.updated_at = utcnow()

    def add_contact(self, contact: ContactMethod) -> None:
        self.contacts.append(contact)
        self.updated_at = utcnow()

    def add_source(self, source: EnrichmentSource) -> None:
        self.sources.append(source)
        self.updated_at = utcnow()

    def log(self, message: str) -> None:
        ts = utcnow().isoformat()
        self.audit_log.append(f"[{ts}] {message}")

    def best_principal_email(self):
        highest = None
        rank = {ContactConfidence.VERIFIED_DIRECT: 3, ContactConfidence.CATCH_ALL: 2, ContactConfidence.UNRESOLVED: 1}
        for c in self.contacts:
            r = rank.get(c.confidence, 0)
            if highest is None or r > rank.get(highest.confidence, 0):
                highest = c
        return highest

    def to_record(self) -> dict:
        data = self.dict()
        data.pop("audit_log", None)
        return data


class SFOCollection(BaseModel):
    entities: list[SFOEntity] = Field(default_factory=list)

    def add(self, entity: SFOEntity) -> None:
        self.entities.append(entity)

    def by_id(self, sfo_id: str):
        for e in self.entities:
            if e.id == sfo_id:
                return e
        return None

    def count(self) -> int:
        return len(self.entities)

    def verified_count(self) -> int:
        return sum(1 for e in self.entities if e.enrichment_status == EnrichmentStatus.COMPLETED)

    def unresolved_contacts_count(self) -> int:
        return sum(
            1 for e in self.entities for c in e.contacts if c.confidence == ContactConfidence.UNRESOLVED
        )
