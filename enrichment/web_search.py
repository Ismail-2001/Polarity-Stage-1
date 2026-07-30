"""Web search client using Serper.dev (free tier: 2500 queries/month).

Falls back gracefully if no API key is configured.
"""

from __future__ import annotations

import json
import re
from typing import Optional, Tuple

import requests

from audit import get_logger
from config.settings import settings


class WebSearchClient:
    """Minimal Serper.dev wrapper for entity enrichment."""

    BASE_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.serper_api_key
        self._log = get_logger("serper_web")
        self._available = bool(self.api_key)

    def search(self, query: str, num: int = 5) -> list[dict]:
        """Search and return structured results (title, link, snippet)."""
        if not self._available:
            self._log.log_failure(
                "serper_web", error="No API key configured — skipping search", entity=""
            )
            return []
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": query, "num": num}
        try:
            resp = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("organic", []):
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                })
            self._log.log_api_call(
                "serper_web", status=resp.status_code, endpoint="search",
                detail=f"query='{query}' -> {len(results)} results",
            )
            return results
        except requests.RequestException as e:
            self._log.log_failure("serper_web", error=str(e), entity="", field=query)
            return []

    def search_principal_email(self, full_name: str, organization: str) -> Optional[Tuple[str, str]]:
        """Returns (email, evidence_snippet) if a non-generic email is found.

        Returns None explicitly (never hallucinates) if not found.
        The evidence_snippet is the search result text that contained the email,
        used downstream to gate VERIFIED_DIRECT confidence on real attribution.
        """
        queries = [
            f'"{full_name}" {organization} email',
            f'"{full_name}" "{organization}" contact',
            f'"{full_name}" email "@{organization.replace(" ", "").lower()}"',
        ]
        for q in queries:
            results = self.search(q, num=3)
            for r in results:
                snippet = r.get("snippet", "").lower()
                link = r.get("link", "").lower()
                emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", snippet + " " + link)
                for e in emails:
                    local = e.split("@")[0].lower().replace(".", "").replace("_", "").replace("-", "")
                    generic = {"info", "contact", "investments", "support", "hello", "admin", "team", "enquiries", "office"}
                    if local not in generic:
                        self._log.log_extraction(
                            source="serper_web", field="email", value=e, entity=full_name,
                            source_url=r.get("link", ""),
                        )
                        return e, snippet  # email + evidence
        return None

    def search_linkedin(self, full_name: str, organization: str) -> Optional[str]:
        """Search for a direct LinkedIn profile URL of a principal."""
        query = f'"{full_name}" {organization} linkedin'
        results = self.search(query, num=3)
        for r in results:
            link = r.get("link", "")
            if re.match(r"^https?:\/\/(www\.)?linkedin\.com\/in\/[a-zA-Z0-9_-]+\/?$", link):
                self._log.log_extraction(
                    source="serper_web", field="linkedin_url", value=link,
                    entity=full_name, source_url=link,
                )
                return link
        return None
