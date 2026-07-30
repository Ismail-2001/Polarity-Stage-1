"""Tests for the SFO/MFO/VC entity-type classifier."""


from enrichment.classifier import (
    MFO_NAME_HINTS,
    MFO_TRIGGERS,
    VC_NAME_HINTS,
    VC_TRIGGERS,
    classify_entity,
    validate_sfo_purity,
)
from models.sfo import EntityType, SFOEntity


class TestClassifierPatterns:
    def test_mfo_trigger_detects_multi_family(self):
        assert MFO_TRIGGERS.search("We are a multi-family office")

    def test_mfo_trigger_detects_external_clients(self):
        assert MFO_TRIGGERS.search("We serve multiple families")

    def test_mfo_trigger_ignores_sfo(self):
        assert not MFO_TRIGGERS.search("Single family office for the Smith family")

    def test_vc_trigger_detects_venture_capital(self):
        assert VC_TRIGGERS.search("Raising a venture capital fund")

    def test_vc_trigger_ignores_family_office(self):
        assert not VC_TRIGGERS.search("Family office making direct investments")

    def test_mfo_name_hint(self):
        assert MFO_NAME_HINTS.search("Consolidated Family Office Services")

    def test_vc_name_hint(self):
        assert VC_NAME_HINTS.search("Smith Family Ventures")


class TestClassifyEntity:
    def test_classify_sfo_by_default(self):
        e = SFOEntity(entity_name="Smith Family Office", source_of_wealth="Manufacturing")
        assert classify_entity(e) == EntityType.SFO

    def test_classify_mfo_by_website_text(self):
        e = SFOEntity(entity_name="Smith Family Office")
        result = classify_entity(e, website_text="multi-family office serving external families")
        assert result == EntityType.MFO

    def test_classify_vc_by_website_text(self):
        e = SFOEntity(entity_name="Tech Investments")
        result = classify_entity(e, website_text="raising venture capital fund for institutional LPs")
        assert result == EntityType.VC

    def test_classify_mfo_by_name_hint(self):
        e = SFOEntity(entity_name="Consolidated Family Wealth Management")
        result = classify_entity(e)
        assert result == EntityType.MFO

    def test_classify_remains_sfo_clean(self):
        e = SFOEntity(entity_name="Cascade Investment", source_of_wealth="Microsoft co-founder")
        assert classify_entity(e) == EntityType.SFO

    def test_classify_remains_sfo_with_innocent_text(self):
        e = SFOEntity(entity_name="Bass Family Office")
        result = classify_entity(e, website_text="preserving and growing our family heritage")
        assert result == EntityType.SFO


class TestValidateSfoPurity:
    def test_valid_sfo_passes(self):
        e = SFOEntity(entity_name="Gates Family Office")
        assert validate_sfo_purity(e) is True

    def test_mfo_flagged(self):
        e = SFOEntity(entity_name="Multi-Family Office Services")
        assert validate_sfo_purity(e) is False

    def test_vc_flagged(self):
        e = SFOEntity(entity_name="Smith Ventures", source_of_wealth="VC fund")
        assert validate_sfo_purity(e) is False
