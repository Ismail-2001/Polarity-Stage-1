"""Seed data loader — reads the 50-record SFO dataset from JSON/CSV."""

from __future__ import annotations

import json
from pathlib import Path

from models.sfo import SFOCollection, SFOEntity


class SeedDataLoader:
    """Loads seed SFO data from JSON or CSV files."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def load_json(self, filename: str = "sfo_seed.json") -> SFOCollection:
        """Load entities from a JSON file (array of SFOEntity dicts)."""
        path = self.data_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Seed file not found: {path}")
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        # Handle both plain arrays and metadata-wrapped format
        if isinstance(raw, dict) and "entities" in raw:
            raw = raw["entities"]
        collection = SFOCollection()
        for item in raw:
            entity = SFOEntity(**item)
            # Preserve existing ID from JSON (don't regenerate)
            if "id" in item and item["id"]:
                entity.id = item["id"]
            collection.add(entity)
        return collection

    def save_json(self, collection: SFOCollection, filename: str = "sfo_enriched.json") -> Path:
        """Persist enriched collection entities to JSON with metadata header."""
        path = self.data_dir / filename
        version_path = self.data_dir / "VERSION"
        version = version_path.read_text().strip() if version_path.exists() else "unknown"

        output = {
            "_metadata": {
                "version": version,
                "entity_count": collection.count(),
                "pipeline": "FamilyOfficePipeline",
            },
            "entities": [e.dict() for e in collection.entities],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2, default=str)
        return path
