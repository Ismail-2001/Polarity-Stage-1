"""Web search clients for entity enrichment.

Primary: Serper.dev (Google search results)
Fallback: Hunter.io (email finder + domain search)

Both fall back gracefully if API keys are not configured.
"""

from __future__ import annotations

import re

import requests

from audit import get_logger
from config.settings import settings

# ── Hunter.io Client ────────────────────────────────────────────────────────

class HunterClient:
    """Hunter.io API client for email discovery.

    Free tier: 25 searches/month, 50 verifications/month.
    Docs: https://hunter.io/api
    """

    BASE_URL = "https://api.hunter.io/v2"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or getattr(settings, "hunter_api_key", "")
        self._log = get_logger("hunter_io")
        self._available = bool(self.api_key)

    def find_email(self, full_name: str, domain: str) -> dict | None:
        """Find email by name + domain using Hunter's Email Finder API.

        Returns {"email": str, "confidence": int, "source": str} or None.
        """
        if not self._available:
            return None

        params = {
            "api_key": self.api_key,
            "first_name": full_name.split()[0] if full_name else "",
            "last_name": " ".join(full_name.split()[1:]) if full_name else "",
            "domain": domain,
        }
        try:
            resp = requests.get(f"{self.BASE_URL}/email-finder", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            email = data.get("email")
            confidence = data.get("confidence", 0)
            if email and confidence >= 50:
                self._log.log_extraction(
                    source="hunter_io", field="email", value=email,
                    entity=full_name, source_url=f"https://hunter.io/{domain}",
                )
                return {
                    "email": email,
                    "confidence": confidence,
                    "source": "hunter_email_finder",
                }
        except requests.RequestException as e:
            self._log.log_failure("hunter_io", error=str(e), entity=full_name)
        return None

    def search_domain(self, domain: str, limit: int = 10) -> list[dict]:
        """Search for emails on a domain using Hunter's Domain Search API.

        Returns list of {"email": str, "first_name": str, "last_name": str, "position": str}.
        """
        if not self._available:
            return []

        params = {
            "api_key": self.api_key,
            "domain": domain,
            "limit": str(limit),
        }
        try:
            resp = requests.get(f"{self.BASE_URL}/domain-search", params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            emails = []
            for item in data.get("emails", []):
                emails.append({
                    "email": item.get("value", ""),
                    "first_name": item.get("first_name", ""),
                    "last_name": item.get("last_name", ""),
                    "position": item.get("position", ""),
                    "confidence": item.get("confidence", 0),
                })
            if emails:
                self._log.log_api_call(
                    "hunter_io", status=resp.status_code, endpoint="domain-search",
                    detail=f"domain={domain} -> {len(emails)} emails",
                )
            return emails
        except requests.RequestException as e:
            self._log.log_failure("hunter_io", error=str(e), entity=domain)
        return []


# ── Serper.dev Client ───────────────────────────────────────────────────────

class WebSearchClient:
    """Serper.dev wrapper for entity enrichment with Hunter.io fallback."""

    BASE_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.serper_api_key
        self._log = get_logger("serper_web")
        self._available = bool(self.api_key)
        self._hunter = HunterClient()

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

    def search_principal_email(self, full_name: str, organization: str) -> tuple[str, str] | None:
        """Returns (email, evidence_snippet) if a non-generic email is found.

        Strategy:
          1. Serper web search (primary)
          2. Hunter.io Email Finder (fallback if Serper fails)
          3. Hunter.io Domain Search (fallback if Email Finder fails)
        """
        # Strategy 1: Serper web search
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

        # Strategy 2: Hunter.io Email Finder (by name + domain)
        domain = organization.replace(" ", "").lower() + ".com"
        hunter_result = self._hunter.find_email(full_name, domain)
        if hunter_result:
            email = hunter_result["email"]
            return email, f"Found via Hunter.io (confidence: {hunter_result['confidence']}%)"

        # Strategy 3: Hunter.io Domain Search (find all emails on domain)
        hunter_emails = self._hunter.search_domain(domain, limit=5)
        for item in hunter_emails:
            email = item.get("email", "")
            first = item.get("first_name", "").lower()
            last = item.get("last_name", "").lower()
            name_parts = full_name.lower().split()
            # Check if this email belongs to the person we're looking for
            if first and last and first in name_parts and last in name_parts:
                return email, f"Found via Hunter.io Domain Search (confidence: {item.get('confidence', 0)}%)"

        return None

    def search_linkedin(self, full_name: str, organization: str) -> str | None:
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
