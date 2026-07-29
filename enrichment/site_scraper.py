"""Company website scraper — extracts principals, contact info, source-of-wealth narrative.

Production features:
  - Exponential backoff retry (3 attempts)
  - SSL error recovery (auto-fallback to verify=False)
  - HTTPS → HTTP scheme fallback
  - Multi-page discovery (/team, /about, /leadership)
  - Rate-limit compliant
"""

from __future__ import annotations

import re
import time
import ssl
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from audit import get_logger
from config.settings import settings

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Pages to probe for team/leadership content
TEAM_PATH_CANDIDATES = [
    "/team", "/about", "/leadership", "/our-team", "/about-us",
    "/management", "/people", "/who-we-are", "/company",
]

GENERIC_MAILBOXES = {
    "info", "contact", "investments", "support", "hello",
    "admin", "team", "enquiries", "office", "mail", "noreply",
}

WEALTH_KEYWORDS = [
    "wealth", "family office", "source of wealth", "fortune",
    "founded", "heritage", "legacy", "philanthropy",
    "multi-generational", "preserve capital", "estate",
]


class SiteScraper:
    """Production-grade company website scraper with resilience."""

    def __init__(self):
        self._log = get_logger("site_scraper")
        self._session = self._build_session()

    # ------------------------------------------------------------------
    # Session factory with retry
    # ------------------------------------------------------------------

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        session.headers.update(HEADERS)
        retry_strategy = Retry(
            total=settings.max_retries,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    # ------------------------------------------------------------------
    # Fetch with multi-strategy resilience
    # ------------------------------------------------------------------

    def fetch_page(self, url: str, timeout: int = 15) -> Optional[BeautifulSoup]:
        """Fetch a URL with SSL fallback and retry support."""
        time.sleep(settings.request_delay_sec)
        strategies = [
            {"verify": True},
            {"verify": False},
        ]
        # If original is HTTPS, try HTTP as last resort
        if url.startswith("https://"):
            http_url = url.replace("https://", "http://", 1)
            strategies.append({"url_override": http_url, "verify": False})
        else:
            strategies.append({"verify": False})

        last_error = None
        for attempt, strategy in enumerate(strategies, 1):
            target_url = strategy.get("url_override", url)
            try:
                resp = self._session.get(
                    target_url,
                    timeout=timeout,
                    verify=strategy["verify"],
                )
                resp.raise_for_status()
                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    self._log.log_failure(
                        "site_scraper",
                        error=f"Non-HTML content ({content_type})",
                        entity=target_url,
                    )
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                if attempt > 1:
                    self._log.log_extraction(
                        source="site_scraper",
                        field="page",
                        value=target_url,
                        entity=target_url,
                        source_url=target_url,
                    )
                return soup
            except requests.exceptions.SSLError as e:
                last_error = f"SSL: {e}"
                self._log.log_failure("site_scraper", error=last_error, entity=target_url)
                continue  # Try next strategy (verify=False or HTTP)
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection: {e}"
                self._log.log_failure("site_scraper", error=last_error, entity=target_url)
                if attempt < len(strategies):
                    time.sleep(1)
                continue
            except requests.RequestException as e:
                last_error = str(e)
                self._log.log_failure("site_scraper", error=last_error, entity=target_url)
                return None  # Non-retryable
        self._log.log_failure("site_scraper", error=f"All strategies failed: {last_error}", entity=url)
        return None

    # ------------------------------------------------------------------
    # Multi-page discovery
    # ------------------------------------------------------------------

    def _discover_pages(self, base_url: str) -> list[BeautifulSoup]:
        """Fetch the homepage + team-relevant subpages."""
        soups = []
        homepage = self.fetch_page(base_url, timeout=20)
        if homepage:
            soups.append(("home", homepage))
        parsed = urlparse(base_url)
        for path in TEAM_PATH_CANDIDATES:
            candidate = f"{parsed.scheme}://{parsed.netloc}{path}"
            soup = self.fetch_page(candidate, timeout=10)
            if soup:
                soups.append((path, soup))
        return soups

    # ------------------------------------------------------------------
    # Principal extraction
    # ------------------------------------------------------------------

    def find_principals(self, soups: list[tuple[str, BeautifulSoup]], domain: str) -> list[dict]:
        """Extract names and titles across all scraped pages."""
        seen = set()
        principals = []
        selectors = [
            ".team-member", ".team-member__name", ".team-member-name",
            ".executive", ".leadership-name", ".founder-name",
            "[class*='team'] [class*='name']", "[class*='leadership'] [class*='name']",
            ".bio__name", ".person-name", ".profile-name",
        ]
        for path, soup in soups:
            for sel in selectors:
                for el in soup.select(sel):
                    name = el.get_text(strip=True)
                    if not name or len(name.split()) < 2 or name.lower() in seen:
                        continue
                    seen.add(name.lower())
                    # Look for adjacent title/role element
                    title_el = el.find_next(
                        ["span", "p", "div", "small"],
                        class_=re.compile(r"(title|role|position|job)", re.I),
                    )
                    title = title_el.get_text(strip=True) if title_el else None
                    if not title:
                        # Try sibling or parent-based title lookup
                        parent = el.parent
                        title_el = parent.find_next(
                            ["span", "p", "div", "small"],
                            class_=re.compile(r"(title|role|position|job)", re.I),
                        )
                        title = title_el.get_text(strip=True) if title_el else None
                    principals.append({"full_name": name, "title": title})
        return principals

    # ------------------------------------------------------------------
    # Email extraction
    # ------------------------------------------------------------------

    def find_emails(self, soups: list[tuple[str, BeautifulSoup]], domain: str) -> list[dict]:
        """Find email addresses with confidence classification."""
        results = []
        seen = set()
        # Pattern 1: visible email in page text
        pat = r"[a-zA-Z0-9._%+-]+@" + re.escape(domain)
        for _, soup in soups:
            text = soup.get_text()
            for m in re.finditer(pat, text, re.IGNORECASE):
                email = m.group(0).lower()
                if email in seen:
                    continue
                seen.add(email)
                is_generic = self._is_generic(email)
                results.append({"email": email, "is_generic": is_generic})
            # Pattern 2: mailto: links
            for a in soup.find_all("a", href=re.compile(r"mailto:")):
                email = a["href"].replace("mailto:", "").strip().lower()
                if email and "@" in email and email not in seen:
                    seen.add(email)
                    is_generic = self._is_generic(email)
                    results.append({"email": email, "is_generic": is_generic})
        return results

    @staticmethod
    def _is_generic(email: str) -> bool:
        local = email.split("@")[0].replace(".", "").replace("_", "").replace("-", "").lower()
        return local in GENERIC_MAILBOXES

    # ------------------------------------------------------------------
    # Wealth narrative extraction
    # ------------------------------------------------------------------

    def extract_wealth_narrative(self, soups: list[tuple[str, BeautifulSoup]]) -> Optional[str]:
        """Extract source-of-wealth narrative from about/mission content."""
        best = ""
        for _, soup in soups:
            for tag in soup.find_all(["p", "div", "section", "article"]):
                text = tag.get_text(strip=True)
                if any(kw in text.lower() for kw in WEALTH_KEYWORDS):
                    if len(text) > len(best):
                        best = text[:500]
        return best if best else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_website(self, url: str) -> dict:
        """Full website enrichment with multi-page discovery."""
        # Normalize URL
        if not url.startswith("http"):
            url = "https://" + url
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")

        pages = self._discover_pages(url)
        if not pages:
            return {"principals": [], "emails": [], "wealth_narrative": None}

        return {
            "principals": self.find_principals(pages, domain),
            "emails": self.find_emails(pages, domain),
            "wealth_narrative": self.extract_wealth_narrative(pages),
            "pages_scraped": len(pages),
        }
