"""Seed data loader — reads the 50-record SFO dataset from JSON/CSV."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from models.sfo import SFOCollection, SFOEntity


class SeedDataLoader:
    """Loads seed SFO data from JSON or CSV files."""

    def __init__(self, data_dir: Union[str, Path]):
        self.data_dir = Path(data_dir)

    def load_json(self, filename: str = "sfo_seed.json") -> SFOCollection:
        """Load entities from a JSON file (array of SFOEntity dicts)."""
        path = self.data_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Seed file not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        collection = SFOCollection()
        for item in raw:
            entity = SFOEntity(**item)
            collection.add(entity)
        return collection

    def save_json(self, collection: SFOCollection, filename: str = "sfo_enriched.json") -> Path:
        """Persist enriched collection entities to JSON as an array."""
        path = self.data_dir / filename
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([e.dict() for e in collection.entities], fh, indent=2, default=str)
        return path
