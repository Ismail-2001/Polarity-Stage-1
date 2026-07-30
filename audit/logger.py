"""Structured audit logger that records every discovery path, API call, extraction, and failure."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class AuditLogger:
    """Writes a structured execution audit trail to both a JSONL file and stderr.

    Usage:
        audit = AuditLogger("pipeline-run-001")
        audit.log_api_call("serper", status=200, entity="ABC")
        audit.log_extraction("sec_edgar", field="aum", value="$500M", source_url="...")
        audit.log_failure("serper", error="Rate limited", entity="ABC")
    """

    def __init__(self, run_name: str, log_dir: Path | None = None):
        if log_dir is None:
            log_dir = Path("audit") / "runs"
        self.run_name = run_name
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._jsonl_path = self.log_dir / f"{run_name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.jsonl"
        self._logger = logging.getLogger(f"audit.{run_name}")
        self._logger.setLevel(logging.DEBUG)
        if not self._logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setLevel(logging.DEBUG)
            fmt = logging.Formatter("%(asctime)s [AUDIT] %(message)s")
            handler.setFormatter(fmt)
            self._logger.addHandler(handler)

    # ------------------------------------------------------------------
    # Core log methods
    # ------------------------------------------------------------------

    def _write(self, record: dict) -> None:
        record["timestamp"] = datetime.now(timezone.utc).isoformat()
        record["run_name"] = self.run_name
        with open(self._jsonl_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        self._logger.debug(json.dumps(record))

    def log_api_call(self, service: str, status: int, entity: str = "", endpoint: str = "", detail: str = "") -> None:
        self._write({
            "event": "api_call",
            "service": service,
            "status": status,
            "entity": entity,
            "endpoint": endpoint,
            "detail": detail,
        })

    def log_extraction(self, source: str, field: str, value: str, entity: str = "", source_url: str = "") -> None:
        self._write({
            "event": "extraction",
            "source": source,
            "field": field,
            "value": value,
            "entity": entity,
            "source_url": source_url,
        })

    def log_failure(self, source: str, error: str, entity: str = "", field: str = "") -> None:
        self._write({
            "event": "failure",
            "source": source,
            "error": error,
            "entity": entity,
            "field": field,
        })

    def log_validation(self, entity: str, field: str, status: str, detail: str = "") -> None:
        self._write({
            "event": "validation",
            "entity": entity,
            "field": field,
            "status": status,
            "detail": detail,
        })

    def log(self, message: str) -> None:
        """Log a generic informational message."""
        self._write({"event": "info", "message": message})

    def log_summary(self, total: int, succeeded: int, failed: int, unresolved: int) -> None:
        self._write({
            "event": "pipeline_summary",
            "total_records": total,
            "succeeded": succeeded,
            "failed": failed,
            "unresolved_contacts": unresolved,
        })

    @property
    def log_path(self) -> Path:
        return self._jsonl_path


# ---------------------------------------------------------------------------
# Module-level default
# ---------------------------------------------------------------------------

_default_logger: AuditLogger | None = None


def get_logger(run_name: str = "default") -> AuditLogger:
    global _default_logger
    if _default_logger is None or _default_logger.run_name != run_name:
        _default_logger = AuditLogger(run_name)
    return _default_logger
