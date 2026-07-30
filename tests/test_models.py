"""Unit tests for core data models — SFO entity validation, contact confidence, guardrails."""

import pytest
from pydantic import ValidationError

from models.sfo import (
    AumConfidence,
    ContactConfidence,
    ContactMethod,
    EntityType,
    Principal,
    SFOEntity,
)
from rag.guardrails import GuardrailLayer

# ===================================================================
# SFOEntity
# ===================================================================

class TestSFOEntity:
    def test_minimal_valid_sfo(self):
        """A valid SFO needs only entity_name."""
        e = SFOEntity(entity_name="Test Family Office")
        assert e.entity_type == EntityType.SFO
        assert e.id.startswith("SFO-")
        assert e.enrichment_status.value == "pending"

    def test_entity_name_required(self):
        with pytest.raises(ValidationError):
            SFOEntity(entity_name="")

    def test_website_validation(self):
        with pytest.raises(ValidationError):
            SFOEntity(entity_name="Bad", website="not-a-url")

    def test_valid_website(self):
        e = SFOEntity(entity_name="Good", website="https://example.com")
        assert e.website == "https://example.com"

    def test_entity_type_discrimination(self):
        e = SFOEntity(entity_name="Test", entity_type=EntityType.SFO)
        assert e.entity_type == EntityType.SFO
        e2 = SFOEntity(entity_name="MFO Test", entity_type=EntityType.MFO)
        assert e2.entity_type == EntityType.MFO

    def test_add_principal(self):
        e = SFOEntity(entity_name="Test")
        p = Principal(full_name="John Doe", title="Founder")
        e.add_principal(p)
        assert len(e.principals) == 1
        assert e.principals[0].full_name == "John Doe"

    def test_add_contact(self):
        e = SFOEntity(entity_name="Test")
        c = ContactMethod(type="email", value="test@example.com", confidence=ContactConfidence.VERIFIED_DIRECT)
        e.add_contact(c)
        assert len(e.contacts) == 1

    def test_best_principal_email_returns_highest_confidence(self):
        e = SFOEntity(entity_name="Test")
        e.add_contact(ContactMethod(type="email", value="info@test.com", confidence=ContactConfidence.CATCH_ALL))
        e.add_contact(ContactMethod(type="email", value="john@test.com", confidence=ContactConfidence.VERIFIED_DIRECT))
        best = e.best_principal_email()
        assert best is not None
        assert best.confidence == ContactConfidence.VERIFIED_DIRECT
        assert best.value == "john@test.com"

    def test_best_principal_email_none_without_contacts(self):
        e = SFOEntity(entity_name="Test")
        assert e.best_principal_email() is None

    def test_log_adds_timestamped_message(self):
        e = SFOEntity(entity_name="Test")
        e.log("Enrichment started")
        assert len(e.audit_log) == 1
        assert "Enrichment started" in e.audit_log[0]

    def test_to_record_excludes_audit_log(self):
        e = SFOEntity(entity_name="Test")
        e.log("something")
        record = e.to_record()
        assert "audit_log" not in record
        assert record["entity_name"] == "Test"

    def test_aum_with_confidence(self):
        e = SFOEntity(
            entity_name="RichFO",
            estimated_aum_usd=1000000000.0,
            aum_confidence=AumConfidence.CONFIRMED,
        )
        assert e.estimated_aum_usd == 1_000_000_000.0
        assert e.aum_confidence == AumConfidence.CONFIRMED

    def test_deterministic_id(self):
        """Same entity_name produces same ID across instances."""
        e1 = SFOEntity(entity_name="Test Family Office")
        e2 = SFOEntity(entity_name="Test Family Office")
        assert e1.id == e2.id
        assert e1.id.startswith("SFO-")

    def test_different_names_different_ids(self):
        e1 = SFOEntity(entity_name="Alpha Family Office")
        e2 = SFOEntity(entity_name="Beta Family Office")
        assert e1.id != e2.id

    def test_last_verified_at_field(self):
        from datetime import datetime, timezone
        e = SFOEntity(entity_name="Test")
        assert e.last_verified_at is None
        e.last_verified_at = datetime.now(timezone.utc)
        assert e.last_verified_at is not None


# ===================================================================
# Principal
# ===================================================================

class TestPrincipal:
    def test_valid_principal(self):
        p = Principal(full_name="Jane Doe", title="CIO")
        assert p.full_name == "Jane Doe"

    def test_invalid_empty_name(self):
        with pytest.raises(ValidationError):
            Principal(full_name="  ")

    def test_linkedin_validation_valid(self):
        p = Principal(full_name="Jane Doe", linkedin_url="https://www.linkedin.com/in/janedoe")
        assert p.linkedin_url == "https://www.linkedin.com/in/janedoe"

    def test_linkedin_validation_invalid(self):
        with pytest.raises(ValidationError):
            Principal(full_name="Jane", linkedin_url="https://linkedin.com/company/somecompany")

    def test_linkedin_validation_invalid_company_page(self):
        with pytest.raises(ValidationError):
            Principal(full_name="Jane", linkedin_url="https://www.linkedin.com/company/acme")


# ===================================================================
# ContactMethod
# ===================================================================

class TestContactMethod:
    def test_valid_email(self):
        c = ContactMethod(type="email", value="john@example.com")
        assert "@" in c.value

    def test_invalid_email_format(self):
        with pytest.raises(ValidationError):
            ContactMethod(type="email", value="not-an-email")

    def test_generic_inbox_downgraded_to_catchall(self):
        c = ContactMethod(type="email", value="info@example.com")
        assert c.confidence == ContactConfidence.CATCH_ALL

    def test_contact_with_explicit_confidence(self):
        c = ContactMethod(type="email", value="john@example.com", confidence=ContactConfidence.VERIFIED_DIRECT)
        assert c.confidence == ContactConfidence.VERIFIED_DIRECT


# ===================================================================
# GuardrailLayer
# ===================================================================

class TestGuardrailLayer:
    def test_hallucination_detected(self):
        g = GuardrailLayer()
        result = g.check_hallucination("The email is probably john@example.com")
        assert result is not None
        assert "speculative" in result.lower()

    def test_hallucination_clean(self):
        g = GuardrailLayer()
        result = g.check_hallucination("The verified email is john@example.com")
        assert result is None

    def test_unresolved_message(self):
        msg = GuardrailLayer.unresolved_message("email")
        assert "unresolved/unverified" in msg
        assert "honestly" not in msg  # the word honestly shouldn't be in the standard message - wait let me check
        # Actually the message says "Information unresolved/unverified in dataset"

    def test_unresolved_message_for_aum(self):
        msg = GuardrailLayer.unresolved_message("AUM")
        assert "AUM" in msg
