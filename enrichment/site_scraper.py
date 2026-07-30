"""Company website scraper — extracts principals, contact info, source-of-wealth narrative.

Production features:
  - Exponential backoff retry (3 attempts)
  - SSL error recovery (auto-fallback to verify=False)
  - HTTPS → HTTP scheme fallback
  - Link-based team page discovery (parses homepage for team/people/about links)
  - Sitemap.xml fallback for team page discovery
  - Rate-limit compliant
"""

from __future__ import annotations

import re
import time
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

# Keywords that indicate a team/leadership/people page in link text or URL
TEAM_LINK_KEYWORDS = re.compile(
    r"(team|people|leadership|leaders|our\s+team|about\s+us|who\s+we\s+are|"
    r"management|executives|staff|personnel|biographies|bios|meet\s+the|"
    r"our\s+people|our\s+leaders|governance|board|advisors|principals)",
    re.IGNORECASE,
)

# Keywords to EXCLUDE from team page discovery (blog, press, legal, etc.)
EXCLUDE_LINK_KEYWORDS = re.compile(
    r"(blog|press|news|media|careers|jobs|legal|privacy|terms|contact|"
    r"login|sign|register|subscribe|newsletter|faq|help|support|"
    r"investors|portfolio|fund|performance|returns|strategies|"
    r"insights|research|market|analysis)",
    re.IGNORECASE,
)

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
    """Production-grade company website scraper with link-based discovery."""

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

    def fetch_page(self, url: str, timeout: int = 15) -> BeautifulSoup | None:
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
    # Link-based team page discovery
    # ------------------------------------------------------------------

    def _discover_team_links(self, homepage: BeautifulSoup, base_url: str) -> list[str]:
        """Parse homepage links to find team/leadership/people pages."""
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc
        discovered = []

        for a_tag in homepage.find_all("a", href=True):
            href = a_tag["href"].strip()
            link_text = a_tag.get_text(strip=True).lower()

            # Skip anchors, javascript, mailto, tel
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            # Resolve relative URLs
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)

            # Stay on same domain
            if parsed.netloc and parsed.netloc != base_domain:
                continue

            # Skip external domains
            if not parsed.netloc:
                full_url = f"{parsed_base.scheme}://{base_domain}{parsed.path}"
                parsed = urlparse(full_url)

            path = parsed.path.lower().rstrip("/")

            # Skip static assets
            if any(ext in path for ext in (".jpg", ".png", ".gif", ".pdf", ".css", ".js", ".svg")):
                continue

            # Score this link for team-page likelihood
            score = 0

            # Check URL path for team keywords
            if TEAM_LINK_KEYWORDS.search(path):
                score += 2

            # Check link text for team keywords
            if TEAM_LINK_KEYWORDS.search(link_text):
                score += 2

            # Penalize excluded keywords
            if EXCLUDE_LINK_KEYWORDS.search(path) or EXCLUDE_LINK_KEYWORDS.search(link_text):
                score -= 3

            if score > 0:
                # Normalize: keep only scheme + netloc + path
                clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                if clean_url not in discovered:
                    discovered.append(clean_url)

        return discovered[:8]  # Cap at 8 team pages

    def _discover_from_sitemap(self, base_url: str) -> list[str]:
        """Try sitemap.xml as fallback for team page discovery."""
        parsed = urlparse(base_url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        try:
            resp = self._session.get(sitemap_url, timeout=10, verify=True)
            resp.raise_for_status()
            # Parse sitemap XML (simple text extraction, not full XML parser)
            text = resp.text
            team_urls = []
            for m in re.finditer(r"<loc>(https?://[^<]+)</loc>", text, re.IGNORECASE):
                url = m.group(1)
                if TEAM_LINK_KEYWORDS.search(url):
                    team_urls.append(url)
            return team_urls[:5]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Multi-page discovery (link-based + sitemap fallback)
    # ------------------------------------------------------------------

    def _discover_pages(self, base_url: str) -> list[tuple[str, BeautifulSoup]]:
        """Fetch homepage, discover team pages from links, fetch them."""
        soups = []

        # Step 1: Fetch homepage
        homepage = self.fetch_page(base_url, timeout=20)
        if not homepage:
            return soups

        soups.append(("home", homepage))

        # Step 2: Discover team pages from homepage links
        team_links = self._discover_team_links(homepage, base_url)

        # Step 3: Fallback to sitemap if no team links found on homepage
        if not team_links:
            team_links = self._discover_from_sitemap(base_url)

        # Step 4: Fetch discovered team pages
        for link in team_links:
            soup = self.fetch_page(link, timeout=10)
            if soup:
                parsed = urlparse(link)
                soups.append((parsed.path, soup))

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

    def extract_wealth_narrative(self, soups: list[tuple[str, BeautifulSoup]]) -> str | None:
        """Extract source-of-wealth narrative from about/mission content."""
        best = ""
        for _, soup in soups:
            for tag in soup.find_all(["p", "div", "section", "article"]):
                text = tag.get_text(strip=True)
                if any(kw in text.lower() for kw in WEALTH_KEYWORDS) and len(text) > len(best):
                    best = text[:500]
        return best if best else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_website(self, url: str) -> dict:
        """Full website enrichment with link-based team page discovery."""
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
