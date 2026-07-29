"""Application configuration loaded from environment variables with sane defaults."""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> datetime:
    """Timezone-aware UTC now. Replaces deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc)


@dataclass
class Settings:
    # Paths
    project_root: Path = Path(__file__).resolve().parent.parent
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("FO_DATA_DIR", "data")))
    chroma_persist_dir: Path = field(default_factory=lambda: Path(os.getenv("CHROMA_PERSIST_DIR", "data/chromadb")))

    # API keys
    serper_api_key: str = os.getenv("SERPER_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")

    # Pipeline control
    pipeline_batch_size: int = int(os.getenv("PIPELINE_BATCH_SIZE", "10"))
    request_delay_sec: float = float(os.getenv("REQUEST_DELAY_SEC", "1.5"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    sec_rate_limit_per_sec: float = float(os.getenv("SEC_RATE_LIMIT_PER_SEC", "10.0"))

    # Feature flags
    enable_sec_enrichment: bool = os.getenv("ENABLE_SEC_ENRICHMENT", "true").lower() == "true"
    enable_web_enrichment: bool = os.getenv("ENABLE_WEB_ENRICHMENT", "true").lower() == "true"

    @property
    def resolved_data_dir(self) -> Path:
        p = self.data_dir if self.data_dir.is_absolute() else self.project_root / self.data_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def resolved_chroma_dir(self) -> Path:
        p = self.chroma_persist_dir if self.chroma_persist_dir.is_absolute() else self.project_root / self.chroma_persist_dir
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
