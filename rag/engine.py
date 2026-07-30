"""Micro-RAG engine for querying the SFO intelligence dataset.

Supports ChromaDB (when available) with an in-memory fallback.
Enforces guardrails, confidence badges, and honest refusal for unresolved fields.

Design: clean separation — swap the store backend without changing query logic.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Optional

from config.settings import settings
from models.sfo import (
    SFOCollection,
    SFOEntity,
    AumConfidence,
    ContactConfidence,
    EnrichmentStatus,
)
from rag.guardrails import GuardrailLayer

# ---------------------------------------------------------------------------
# Attempt ChromaDB import; fall back to in-memory store
# ---------------------------------------------------------------------------

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False


# ---------------------------------------------------------------------------
# Confidence badge helper
# ---------------------------------------------------------------------------

CONFIDENCE_BADGES = {
    ContactConfidence.VERIFIED_DIRECT: "[Verified Direct Email]",
    ContactConfidence.CATCH_ALL: "[Catch-all / Generic Inbox]",
    ContactConfidence.UNRESOLVED: "[Unresolved Contact]",
    ContactConfidence.UNVERIFIED: "[Unverified]",
}


def _confidence_badge(conf: ContactConfidence) -> str:
    return CONFIDENCE_BADGES.get(conf, "[Unknown]")


def _entity_to_document(entity: SFOEntity) -> tuple[str, dict, str]:
    """Convert an SFO entity to a (document, metadata, id) tuple for indexing."""
    lines = [
        f"Entity: {entity.entity_name}",
        f"Type: {entity.entity_type.value}",
        f"Family: {entity.family_name or 'N/A'}",
        f"Wealth Source: {entity.source_of_wealth or 'N/A'}",
    ]
    if entity.estimated_aum_usd:
        lines.append(f"AUM: ${entity.estimated_aum_usd:,.0f}")
    else:
        lines.append("AUM: N/A")
    lines.append(f"AUM Confidence: {entity.aum_confidence.value}")
    lines.append(f"HQ: {entity.hq_city or 'N/A'}, {entity.hq_country or 'N/A'}")
    lines.append(f"Website: {entity.website or 'N/A'}")
    lines.append(f"Year Est: {entity.year_established or 'N/A'}")

    for p in entity.principals:
        lines.append(f"Principal: {p.full_name} ({p.title or 'N/A'})")
        if p.linkedin_url:
            lines.append(f"  LinkedIn: {p.linkedin_url}")
    for c in entity.contacts:
        lines.append(f"Contact: {c.value} {_confidence_badge(c.confidence)}")
    lines.append(f"Enrichment: {entity.enrichment_status.value}")

    document = "\n".join(lines)
    metadata = {
        "id": entity.id,
        "entity_name": entity.entity_name,
        "entity_type": entity.entity_type.value,
        "family_name": entity.family_name or "",
        "source_of_wealth": entity.source_of_wealth or "",
        "aum": entity.estimated_aum_usd or 0.0,
        "aum_confidence": entity.aum_confidence.value,
        "hq_city": entity.hq_city or "",
        "hq_country": entity.hq_country or "",
        "enrichment_status": entity.enrichment_status.value,
        "has_unresolved_contact": any(
            c.confidence == ContactConfidence.UNRESOLVED for c in entity.contacts
        ),
        "verified_contact_count": sum(
            1 for c in entity.contacts if c.confidence == ContactConfidence.VERIFIED_DIRECT
        ),
        "principal_count": len(entity.principals),
    }
    return document, metadata, entity.id


# ---------------------------------------------------------------------------
# In-memory store (fallback when ChromaDB unavailable)
# ---------------------------------------------------------------------------

class _InMemoryStore:
    """Simple in-memory vector-like store with text search fallback."""

    def __init__(self):
        self._docs: dict[str, str] = {}
        self._metadatas: dict[str, dict] = {}

    def upsert(self, documents: list[str], metadatas: list[dict], ids: list[str]) -> None:
        for doc, meta, eid in zip(documents, metadatas, ids):
            self._docs[eid] = doc
            self._metadatas[eid] = meta

    def count(self) -> int:
        return len(self._docs)

    def get(self, ids: Optional[list[str]] = None, limit: int = 50, offset: int = 0):
        if ids:
            result_ids = [i for i in ids if i in self._docs]
        else:
            result_ids = list(self._docs.keys())[offset:offset + limit]
        return {
            "ids": result_ids,
            "documents": [self._docs[i] for i in result_ids],
            "metadatas": [self._metadatas.get(i, {}) for i in result_ids],
            "distances": [0.0] * len(result_ids),
        }

    def query(self, query_text: str, n_results: int = 5):
        """TF-IDF-like keyword retrieval with field-aware boosting."""
        import math
        query_lower = query_text.lower()
        query_terms = re.findall(r'\w+', query_lower)
        query_term_set = set(query_terms)
        n_docs = len(self._docs) or 1

        # Compute IDF for each query term
        idf: dict[str, float] = {}
        for qt in query_term_set:
            df = sum(1 for doc in self._docs.values() if qt in doc.lower())
            idf[qt] = math.log((n_docs + 1) / (df + 1)) + 1.0

        scored = []
        for eid, doc in self._docs.items():
            doc_lower = doc.lower()
            meta = self._metadatas.get(eid, {})
            entity_name = (meta.get("entity_name", "") or "").lower()
            aum = meta.get("aum", 0)

            score = 0.0
            for qt, weight in idf.items():
                # TF in document
                tf = doc_lower.count(qt)
                score += tf * weight * 0.01

            # --- Boosts ---

            # 1. Exact phrase match (high signal)
            if query_lower in doc_lower:
                score += 1.5

            # 2. Entity name prefix match (very high signal)
            for qt in query_term_set:
                if entity_name.startswith(qt) or qt in entity_name.split():
                    score += 2.0

            # 3. Multi-word phrase match (e.g. "single family" in doc)
            if len(query_terms) > 1:
                for start in range(len(query_terms) - 1):
                    phrase = " ".join(query_terms[start:start + 2])
                    if phrase in doc_lower:
                        score += 0.8

            # 4. Wealth source match
            wealth = (meta.get("source_of_wealth", "") or "").lower()
            for qt in query_term_set:
                if qt in wealth:
                    score += 1.0

            # 5. HQ city/country match
            hq_city = (meta.get("hq_city", "") or "").lower()
            hq_country = (meta.get("hq_country", "") or "").lower()
            for qt in query_term_set:
                if qt in hq_city or qt in hq_country:
                    score += 0.5

            # 6. AUM proximity boost
            for m in re.finditer(r'\$?([0-9,]+(?:\.[0-9]+)?)\s*(billion|million|B|M)', doc_lower):
                try:
                    doc_aum = float(m.group(1).replace(",", ""))
                    unit = (m.group(2) or "").lower()
                    if unit in ("billion", "b"):
                        doc_aum *= 1_000_000_000
                    elif unit in ("million", "m"):
                        doc_aum *= 1_000_000
                    # Check if query mentions a number
                    for qt in query_terms:
                        if qt.isdigit() and abs(float(qt) * 1_000_000_000 - doc_aum) < doc_aum * 0.5:
                            score += 1.0
                except ValueError:
                    pass

            scored.append((score, eid))

        scored.sort(key=lambda x: -x[0])
        top = scored[:n_results]

        result_ids = [eid for _, eid in top]
        max_score = max((s for s, _ in top), default=1.0)
        return {
            "ids": [result_ids],
            "documents": [[self._docs[eid] for eid in result_ids]],
            "metadatas": [[self._metadatas.get(eid, {}) for eid in result_ids]],
                "distances": [[max(0.0, 1.0 - (s / max_score)) if max_score > 0 else 0.0 for s, _ in top]],
        }


# ---------------------------------------------------------------------------
# MicroRAGEngine
# ---------------------------------------------------------------------------

class MicroRAGEngine:
    """Retrieval engine with ChromaDB backend (or in-memory fallback)."""

    def __init__(self, persist_dir: Optional[Path] = None):
        self._persist_dir = persist_dir or settings.resolved_chroma_dir
        self._guardrails = GuardrailLayer()

        if HAS_CHROMA:
            self._client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                name="sfo_entities",
                metadata={"hnsw:space": "cosine"},
            )
            self._store = None  # using ChromaDB directly
        else:
            self._client = None
            self._collection = None
            self._store = _InMemoryStore()

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def index_collection(self, collection: SFOCollection) -> int:
        docs, metadatas, ids = [], [], []
        for entity in collection.entities:
            doc, meta, eid = _entity_to_document(entity)
            docs.append(doc)
            metadatas.append(meta)
            ids.append(eid)

        if HAS_CHROMA:
            self._collection.upsert(documents=docs, metadatas=metadatas, ids=ids)
        else:
            self._store.upsert(documents=docs, metadatas=metadatas, ids=ids)

        return len(ids)

    def count(self) -> int:
        if HAS_CHROMA:
            return self._collection.count()
        return self._store.count() if self._store else 0

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        min_confidence: Optional[AumConfidence] = None,
    ) -> dict:
        """Query the SFO knowledge base with guardrail enforcement.

        Returns:
            dict with keys: query, result_count, results, guardrail_notes
        """
        guardrail_notes = []

        if self.count() == 0:
            guardrail_notes.append("Dataset is empty — no SFO records indexed.")
            return {"query": query_text, "result_count": 0, "results": [], "guardrail_notes": guardrail_notes}

        # Run retrieval
        if HAS_CHROMA:
            raw = self._collection.query(query_texts=[query_text], n_results=n_results)
        else:
            raw = self._store.query(query_text=query_text, n_results=n_results)

        results = []
        for i in range(len(raw["ids"][0])):
            doc = raw["documents"][0][i]
            meta = raw["metadatas"][0][i]
            eid = raw["ids"][0][i]
            distance = raw["distances"][0][i] if raw.get("distances") else 0.0

            unresolved_warning = ""
            if meta.get("has_unresolved_contact"):
                unresolved_warning = (
                    "⚠ Information unresolved/unverified in dataset — "
                    "this entity has contacts marked [Unresolved Contact]."
                )
                guardrail_notes.append(unresolved_warning)

            # Confidence filter
            if min_confidence and meta.get("aum_confidence") == "Unresolved":
                if min_confidence == AumConfidence.CONFIRMED:
                    guardrail_notes.append(
                        f"⚠ {meta.get('entity_name', 'Unknown')}: AUM is Unresolved — "
                        "skipped due to VERIFIED_DIRECT confidence filter."
                    )
                    continue

            results.append({
                "id": eid,
                "entity_name": meta.get("entity_name", ""),
                "entity_type": meta.get("entity_type", ""),
                "family_name": meta.get("family_name", ""),
                "source_of_wealth": meta.get("source_of_wealth", ""),
                "aum": meta.get("aum", 0.0),
                "aum_confidence": meta.get("aum_confidence", ""),
                "hq": f"{meta.get('hq_city', '')}, {meta.get('hq_country', '')}",
                "principal_count": meta.get("principal_count", 0),
                "verified_contact_count": meta.get("verified_contact_count", 0),
                "has_unresolved_contact": meta.get("has_unresolved_contact", False),
                "enrichment_status": meta.get("enrichment_status", ""),
                "raw_document": doc,
                "similarity_score": round(1.0 - distance, 4),
                "unresolved_warning": unresolved_warning,
            })

        # De-duplicate by normalizing (lowercase, strip leading emoji/whitespace)
        seen: set[str] = set()
        deduped: list[str] = []
        for note in guardrail_notes:
            key = note.lower().strip().lstrip("\u26a0\ufe0f ").strip()
            if key not in seen:
                seen.add(key)
                deduped.append(note)
        guardrail_notes = self._guardrails.enforce(deduped)

        return {
            "query": query_text,
            "result_count": len(results),
            "results": results,
            "guardrail_notes": guardrail_notes,
        }

    def get_entity(self, entity_id: str) -> Optional[dict]:
        if HAS_CHROMA:
            raw = self._collection.get(ids=[entity_id])
        else:
            raw = self._store.get(ids=[entity_id])
        if not raw["ids"]:
            return None
        return {
            "id": raw["ids"][0],
            "document": raw["documents"][0] if raw.get("documents") else "",
            "metadata": raw["metadatas"][0] if raw.get("metadatas") else {},
        }

    def list_entities(self, limit: int = 50, offset: int = 0) -> list[dict]:
        if HAS_CHROMA:
            raw = self._collection.get(limit=limit, offset=offset)
        else:
            raw = self._store.get(limit=limit, offset=offset)
        entries = []
        for i in range(len(raw["ids"])):
            entries.append({
                "id": raw["ids"][i],
                "metadata": raw["metadatas"][i] if raw.get("metadatas") else {},
            })
        return entries
