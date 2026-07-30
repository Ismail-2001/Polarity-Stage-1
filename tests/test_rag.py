"""Tests for the ChromaDB Micro-RAG engine and guardrail layer."""

import tempfile
from pathlib import Path

from models.sfo import (
    AumConfidence,
    ContactConfidence,
    ContactMethod,
    EnrichmentStatus,
    Principal,
    SFOCollection,
    SFOEntity,
)
from rag.engine import MicroRAGEngine, _confidence_badge
from rag.guardrails import GuardrailLayer


def _make_test_collection() -> SFOCollection:
    c = SFOCollection()
    e1 = SFOEntity(
        entity_name="Tech Family Office",
        family_name="TestFamily",
        source_of_wealth="Technology investments",
        estimated_aum_usd=500_000_000,
        aum_confidence=AumConfidence.CONFIRMED,
        hq_city="San Francisco",
        enrichment_status=EnrichmentStatus.COMPLETED,
    )
    e1.add_principal(Principal(full_name="John Test", title="Founder"))
    e1.add_contact(
        ContactMethod(type="email", value="john@test.com", confidence=ContactConfidence.VERIFIED_DIRECT)
    )
    c.add(e1)

    e2 = SFOEntity(
        entity_name="Private SFO",
        family_name="Private",
        source_of_wealth="Real estate",
        estimated_aum_usd=None,
        aum_confidence=AumConfidence.UNRESOLVED,
        hq_city="Miami",
        enrichment_status=EnrichmentStatus.COMPLETED,
    )
    e2.add_contact(
        ContactMethod(type="email", value="Unresolved", confidence=ContactConfidence.UNRESOLVED,
                      notes="No verified contact found")
    )
    c.add(e2)
    return c


class TestMicroRAGEngine:
    def test_index_and_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MicroRAGEngine(persist_dir=Path(tmpdir))
            coll = _make_test_collection()
            count = engine.index_collection(coll)
            assert count == 2
            assert engine.count() == 2

    def test_query_returns_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MicroRAGEngine(persist_dir=Path(tmpdir))
            engine.index_collection(_make_test_collection())
            result = engine.query("technology family office")
            assert result["result_count"] > 0
            assert result["results"][0]["entity_name"] == "Tech Family Office"

    def test_query_guardrail_for_unresolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MicroRAGEngine(persist_dir=Path(tmpdir))
            engine.index_collection(_make_test_collection())
            result = engine.query("private sfo")
            has_unresolved = any(r.get("has_unresolved_contact") for r in result["results"])
            unresolved_warning = any(
                "unresolved/unverified" in n.lower() for n in result.get("guardrail_notes", [])
            )
            # At least one of these should be true
            assert has_unresolved or unresolved_warning

    def test_empty_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MicroRAGEngine(persist_dir=Path(tmpdir))
            result = engine.query("anything")
            assert "Dataset is empty" in result["guardrail_notes"][0]
            assert result["result_count"] == 0

    def test_get_entity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MicroRAGEngine(persist_dir=Path(tmpdir))
            coll = _make_test_collection()
            engine.index_collection(coll)
            entity = engine.get_entity(coll.entities[0].id)
            assert entity is not None
            assert entity["metadata"]["entity_name"] == "Tech Family Office"

    def test_get_entity_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MicroRAGEngine(persist_dir=Path(tmpdir))
            entity = engine.get_entity("SFO-NONEXISTENT")
            assert entity is None

    def test_list_entities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = MicroRAGEngine(persist_dir=Path(tmpdir))
            coll = _make_test_collection()
            engine.index_collection(coll)
            entities = engine.list_entities()
            assert len(entities) == 2


class TestConfidenceBadge:
    def test_verified_badge(self):
        assert "[Verified Direct Email]" in _confidence_badge(ContactConfidence.VERIFIED_DIRECT)

    def test_catchall_badge(self):
        assert "[Catch-all" in _confidence_badge(ContactConfidence.CATCH_ALL)

    def test_unresolved_badge(self):
        assert "[Unresolved" in _confidence_badge(ContactConfidence.UNRESOLVED)


class TestGuardrailLayer:
    def test_enforce_passes_clean_notes(self):
        g = GuardrailLayer()
        notes = ["verified data"]
        assert g.enforce(notes) == notes

    def test_enforce_detects_hallucination(self):
        g = GuardrailLayer()
        result = g.enforce(["I think the email might be john@test.com"])
        assert len(result) > 1
        assert any("Guardrail triggered" in n for n in result)

    def test_enforce_detects_generic_contact(self):
        g = GuardrailLayer()
        result = g.enforce(["contact info@company.com"])
        assert any("generic inbox" in n.lower() for n in result)

    def test_check_hallucination_found(self):
        g = GuardrailLayer()
        assert g.check_hallucination("I think the email is...") is not None
        assert g.check_hallucination("probably works") is not None
        assert g.check_hallucination("might be correct") is not None

    def test_check_hallucination_clean(self):
        g = GuardrailLayer()
        assert g.check_hallucination("The verified email is john@test.com") is None

    def test_unresolved_message(self):
        msg = GuardrailLayer.unresolved_message("contact")
        assert "unresolved/unverified" in msg
