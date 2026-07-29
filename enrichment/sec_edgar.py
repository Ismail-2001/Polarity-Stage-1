"""SEC EDGAR client — modern JSON API with HTML fallback.

Uses the SEC's structured JSON endpoints (submissions, companyfacts) as the
primary data source. Falls back to HTML scraping for Form ADV AUM extraction.

Rate-limited to 10 req/s (SEC fair-access policy). Always sets a compliant
User-Agent header.

References:
  https://www.sec.gov/edgar/sec-api-documentation
  https://www.sec.gov/os/webmaster-faq
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from audit import get_logger
from config.settings import settings

# ── SEC endpoints ────────────────────────────────────────────────────────────

SEC_CIK_LOOKUP = "https://www.sec.gov/cgi-bin/cik_lookup"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{}.json"
SEC_COMPANY_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{}.json"
SEC_EDGAR_BROWSE = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

USER_AGENT = (
    "FamilyOfficePipeline/1.0 (research; "
    "contact@familyofficepipeline.dev; "
    "https://github.com/anomalyco/fo-intelligence)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


class SECEdgarClient:
    """Production-grade SEC EDGAR client with modern JSON API support."""

    def __init__(self):
        self._log = get_logger("sec_edgar")
        self._last_request = 0.0
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    # ── Rate limiting ─────────────────────────────────────────────────────

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request
        min_interval = 1.0 / max(settings.sec_rate_limit_per_sec, 1.0)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request = time.time()

    def _req(self, url: str, timeout: int = 20) -> Optional[requests.Response]:
        self._rate_limit()
        try:
            resp = self._session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            self._log.log_failure("sec_edgar", error=str(e), entity=url)
            return None

    # ── CIK: multi-variant resolution ────────────────────────────────────

    def lookup_cik(self, name_variants: list[str]) -> Optional[str]:
        """Try multiple name variants and return the first CIK found.

        For a family office named "Smith Family Office", try:
          ["Smith Family Office", "Smith", "Smith Investments",
           "Smith Foundation", "Smith Company"]
        """
        for variant in name_variants:
            cik = self._lookup_cik_single(variant)
            if cik:
                return cik
        return None

    def _lookup_cik_single(self, company_name: str) -> Optional[str]:
        """Resolve a single company name to its CIK."""
        self._rate_limit()
        params = {"company": company_name, "action": "getcompany"}
        try:
            resp = self._session.get(SEC_CIK_LOOKUP, params=params, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"cik_lookup")):
                text = a.get_text(strip=True)
                if text.isdigit():
                    cik = text.zfill(10)
                    self._log.log_extraction(
                        source="sec_edgar", field="cik", value=cik,
                        entity=company_name, source_url=resp.url,
                    )
                    return cik
        except requests.RequestException:
            pass
        return None

    def _generate_name_variants(self, family_name: str, entity_name: str) -> list[str]:
        """Generate search variants from family/entity names."""
        variants = []
        seen = set()
        candidates = [entity_name, family_name] if family_name else [entity_name]
        for base in candidates:
            base = base.strip()
            if not base or base in seen:
                continue
            seen.add(base)
            variants.append(base)
            # Strip common suffixes
            for suffix in [" Family Office", " Investment", " Capital", " Group",
                           " LLC", " Inc.", " Ltd.", " LP", " Trust"]:
                stripped = re.sub(re.escape(suffix) + r"$", "", base, flags=re.IGNORECASE).strip()
                if stripped and stripped != base and stripped not in seen:
                    seen.add(stripped)
                    variants.append(stripped)
        return variants

    # ── Submissions JSON API ─────────────────────────────────────────────

    def get_submissions(self, cik: str) -> Optional[dict]:
        """Fetch the SEC submissions JSON for a CIK."""
        url = SEC_SUBMISSIONS.format(cik)
        resp = self._req(url)
        if resp:
            try:
                return resp.json()
            except json.JSONDecodeError as e:
                self._log.log_failure("sec_edgar", error=f"JSON decode: {e}", entity=cik)
        return None

    def get_company_facts(self, cik: str) -> Optional[dict]:
        """Fetch XBRL company facts JSON (contains structured AUM data)."""
        url = SEC_COMPANY_FACTS.format(cik)
        resp = self._req(url)
        if resp:
            try:
                return resp.json()
            except json.JSONDecodeError:
                return None
        return None

    # ── AUM extraction (multi-strategy) ──────────────────────────────────

    def extract_aum(self, entity_name: str, family_name: Optional[str] = None) -> Optional[float]:
        """Multi-strategy AUM extraction.

        Strategy 1: XBRL company facts (most reliable)
        Strategy 2: Form ADV HTML scraping (fallback)
        Strategy 3: 13F filing total value
        """
        variants = self._generate_name_variants(family_name or "", entity_name)
        cik = self.lookup_cik(variants)
        if not cik:
            self._log.log_failure(
                "sec_edgar", error="No CIK found for any name variant",
                entity=entity_name,
            )
            return None

        # Strategy 1: XBRL facts
        aum = self._extract_aum_from_facts(cik)
        if aum is not None:
            return aum

        # Strategy 2: Form ADV
        aum = self.extract_aum_from_adv(cik)
        if aum is not None:
            return aum

        # Strategy 3: 13F (rough estimate)
        aum = self._extract_aum_from_13f(cik)
        if aum is not None:
            return aum

        return None

    def _extract_aum_from_facts(self, cik: str) -> Optional[float]:
        """Extract AUM from XBRL company facts JSON (Item 5.B of Form ADV)."""
        facts = self.get_company_facts(cik)
        if not facts:
            return None
        try:
            # Look for AUM-related facts
            facts_data = facts.get("facts", {})
            for namespace in ("us-gaap", "adv"):
                ns = facts_data.get(namespace, {})
                for concept, data in ns.items():
                    label = (data.get("label", "") or "").lower()
                    desc = (data.get("description", "") or "").lower()
                    if any(kw in label or kw in desc for kw in
                           ["regulatory assets under management", "aum",
                            "assets under management", "total assets"]):
                        units = data.get("units", {})
                        for unit_key, values in units.items():
                            if values:
                                latest = values[-1]
                                val = latest.get("val")
                                if val is not None:
                                    self._log.log_extraction(
                                        source="sec_edgar_xbrl", field="aum",
                                        value=str(val), entity=cik,
                                    )
                                    return float(val)
        except (KeyError, IndexError, TypeError):
            pass
        return None

    def extract_aum_from_adv(self, cik: str) -> Optional[float]:
        """Extract AUM from Form ADV Part 1 filing via HTML scraping."""
        filings = self._search_filings(cik, form_type="ADV")
        if not filings:
            return None
        latest = filings[0]
        accession = latest.get("accession", "")
        if not accession:
            return None
        doc_url = (
            f"{SEC_ARCHIVES}/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}-index.html"
        )
        self._rate_limit()
        try:
            resp = self._session.get(doc_url, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text()
            # Multiple AUM patterns used in ADV filings
            patterns = [
                # Item 5: Regulatory Assets Under Management
                r"Regulatory Assets Under Management[:\s]*\$?([0-9,]+(?:\.[0-9]+)?)\s*(million|billion|M|B|mm|bn)?",
                r"Item\s+5[.A-Z]*[:\s]*\$?([0-9,]+(?:\.[0-9]+)?)\s*(million|billion|M|B|mm|bn)?",
                # Total assets (all ADV formats)
                r"Total\s+assets[:\s]*\$?([0-9,]+(?:\.[0-9]+)?)\s*(million|billion|M|B|mm|bn)?",
                # Approximate AUM
                r"approximately\s*\$?([0-9,]+(?:\.[0-9]+)?)\s*(million|billion|M|B)?",
            ]
            for pat in patterns:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    amount = float(m.group(1).replace(",", ""))
                    unit = (m.group(2) or "").lower()
                    if unit in ("billion", "b", "bn"):
                        amount *= 1_000_000_000
                    elif unit in ("million", "m", "mm"):
                        amount *= 1_000_000
                    self._log.log_extraction(
                        source="sec_edgar_html", field="aum", value=str(amount),
                        entity=cik, source_url=doc_url,
                    )
                    return amount
        except requests.RequestException:
            pass
        return None

    def _extract_aum_from_13f(self, cik: str) -> Optional[float]:
        """Estimate AUM from the most recent 13F filing total value."""
        filings = self._search_filings(cik, form_type="13F-HR")
        if not filings:
            return None
        latest = filings[0]
        accession = latest.get("accession", "")
        if not accession:
            return None
        # 13F summary page
        doc_url = (
            f"{SEC_ARCHIVES}/{int(cik)}/"
            f"{accession.replace('-', '')}/{accession}-index.html"
        )
        self._rate_limit()
        try:
            resp = self._session.get(doc_url, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            text = soup.get_text()
            # Look for "Total" or "Value" followed by a dollar amount
            m = re.search(
                r"(?:Total|Value|Sum)[:\s]*\$?([0-9,]+(?:\.[0-9]+)?)\s*(million|billion|M|B|thousand|K)?",
                text, re.IGNORECASE,
            )
            if m:
                amount = float(m.group(1).replace(",", ""))
                unit = (m.group(2) or "").lower()
                if unit in ("billion", "b"):
                    amount *= 1_000_000_000
                elif unit in ("million", "m"):
                    amount *= 1_000_000
                elif unit in ("thousand", "k"):
                    amount *= 1_000
                return amount
        except requests.RequestException:
            pass
        return None

    # ── Filing search ────────────────────────────────────────────────────

    def _search_filings(self, cik: str, form_type: str = "ADV") -> list[dict]:
        """Search recent filings via the submissions JSON API."""
        submissions = self.get_submissions(cik)
        if not submissions:
            return []
        recent = submissions.get("filings", {}).get("recent", {})
        form_types = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])

        filings = []
        for i in range(len(form_types)):
            if form_types[i] == form_type or form_type in form_types[i]:
                accession = accession_numbers[i] if i < len(accession_numbers) else ""
                filings.append({
                    "form": form_types[i],
                    "filing_date": filing_dates[i] if i < len(filing_dates) else "",
                    "accession": accession,
                    "primary_document": primary_docs[i] if i < len(primary_docs) else "",
                })
        self._log.log_api_call(
            "sec_edgar", status=200, entity=cik, endpoint="search_filings",
            detail=f"Found {len(filings)} {form_type} filings via submissions API",
        )
        return filings
