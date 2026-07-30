"""Tests for seed data loading and pipeline orchestration."""

from pathlib import Path

import pytest

from models.sfo import AumConfidence, ContactConfidence, SFOCollection, SFOEntity
from pipeline.loader import SeedDataLoader


class TestSeedDataLoader:
    def test_load_50_seed_records(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        loader = SeedDataLoader(data_dir)
        collection = loader.load_json("sfo_seed.json")
        assert collection.count() == 50, f"Expected 50 seed records, got {collection.count()}"

    def test_all_seed_records_are_sfo(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        loader = SeedDataLoader(data_dir)
        collection = loader.load_json("sfo_seed.json")
        non_sfo = [e for e in collection.entities if e.entity_type.value != "SFO"]
        assert len(non_sfo) == 0, f"Found non-SFO entities: {[e.entity_name for e in non_sfo]}"

    def test_seed_records_have_entity_name(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        loader = SeedDataLoader(data_dir)
        collection = loader.load_json("sfo_seed.json")
        for e in collection.entities:
            assert e.entity_name, f"Entity {e.id} has no entity_name"

    def test_some_contacts_are_unresolved(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        loader = SeedDataLoader(data_dir)
        collection = loader.load_json("sfo_seed.json")
        unresolved = collection.unresolved_contacts_count()
        assert unresolved > 0, "Expected some unresolved contacts in seed data"
        print(f"Unresolved contacts in seed: {unresolved}")

    def test_some_contacts_are_catch_all(self):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        loader = SeedDataLoader(data_dir)
        collection = loader.load_json("sfo_seed.json")
        catch_all = sum(
            1 for e in collection.entities
            for c in e.contacts
            if c.confidence == ContactConfidence.CATCH_ALL
        )
        assert catch_all > 0, "Expected some catch-all contacts in seed data"
        print(f"Catch-all contacts in seed: {catch_all}")

    def test_save_and_reload_roundtrip(self, tmp_path):
        data_dir = Path(__file__).resolve().parent.parent / "data"
        loader = SeedDataLoader(data_dir)
        collection = loader.load_json("sfo_seed.json")
        # Save to temp
        tmp_loader = SeedDataLoader(tmp_path)
        path = tmp_loader.save_json(collection, "test_roundtrip.json")
        assert path.exists()
        # Reload
        reloaded = tmp_loader.load_json("test_roundtrip.json")
        assert reloaded.count() == collection.count()
        assert reloaded.entities[0].entity_name == collection.entities[0].entity_name

    def test_aum_confidence_never_falsified(self):
        """Ensure no entity has Verified AUM without a realistic source."""
        data_dir = Path(__file__).resolve().parent.parent / "data"
        loader = SeedDataLoader(data_dir)
        source = "sfo_enriched.json" if (data_dir / "sfo_enriched.json").exists() else "sfo_seed_DEPRECATED_famous_names.json"
        collection = loader.load_json(source)
        for e in collection.entities:
            if e.aum_confidence == AumConfidence.CONFIRMED:
                assert e.estimated_aum_usd is not None and e.estimated_aum_usd > 0, (
                    f"{e.entity_name}: Verified AUM confidence but no AUM value"
                )
