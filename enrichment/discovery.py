"""SEC EDGAR IA bulk discovery — replaces the fabricated 50-entry seed list.

Searches the SEC Elasticsearch Full-Text Search (EFTS) API for recent Form ADV
filings mentioning "family office", extracts CIK numbers and entity names,
then enriches each candidate with AUM data via the existing SECEdgarClient.

Returns SFOEntity-compatible seed dicts sorted by AUM descending.
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import urlencode

import requests

from audit import get_logger
from enrichment.sec_edgar import SECEdgarClient, USER_AGENT, SEC_SUBMISSIONS

SEC_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

SEC_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}

KNOWN_FAMILY_OFFICE_KEYWORDS = [
    "family office",
    "family investment",
    "family capital",
    "private family",
    "single family office",
    "multi family office",
]

SFO_SUFFIX_PATTERNS = [
    re.compile(r"\bFamily\s*Office\b", re.IGNORECASE),
    re.compile(r"\bFamily\s*Investment\b", re.IGNORECASE),
    re.compile(r"\bFamily\s*Capital\b", re.IGNORECASE),
]


def _rate_limit() -> None:
    time.sleep(0.15)


def _search_efts(query: str, start: int = 0, page_size: int = 100) -> Optional[dict]:
    """Execute a SEC EFTS query and return the JSON response."""
    params = {
        "q": query,
        "dateRange": "all",
        "start": start,
        "counts": min(page_size, 100),
    }
    url = f"{SEC_EFTS_URL}?{urlencode(params)}"
    _rate_limit()
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def _extract_hit(hit: dict) -> Optional[dict]:
    """Extract {cik, company_name, form_type, filing_date} from an EFTS hit."""
    src = hit.get("_source", {})
    cik = src.get("cik")
    if not cik:
        return None
    name = src.get("entity_name") or src.get("company_name") or ""
    form = src.get("form") or src.get("form_type") or ""
    fdate = src.get("file_date") or src.get("filing_date") or ""
    return {
        "cik": str(cik).zfill(10),
        "company_name": name.strip(),
        "form_type": form,
        "filing_date": fdate,
    }


def _is_sfo_candidate(name: str) -> bool:
    """Heuristic check whether a company name suggests a family office."""
    if not name:
        return False
    lower = name.lower()
    for kw in KNOWN_FAMILY_OFFICE_KEYWORDS:
        if kw in lower:
            return True
    for pat in SFO_SUFFIX_PATTERNS:
        if pat.search(name):
            return True
    return False


def _extract_hq_from_submissions(cik: str) -> Optional[str]:
    """Extract business city/state from SEC submissions address data."""
    url = SEC_SUBMISSIONS.format(cik)
    _rate_limit()
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        addresses = data.get("addresses", {})
        business = addresses.get("business", {})
        city = business.get("city", "") or ""
        state = business.get("stateOrCountry", "") or ""
        if city:
            return f"{city}, {state}".strip(", ")
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None


def run_discovery(max_candidates: int = 50) -> list[dict]:
    """Discover SFO entities by querying SEC IA bulk filings.

    Strategy:
      1. Search EFTS for recent Form ADV filings mentioning "family office".
      2. Deduplicate by CIK; retain company name and filing date.
      3. For each candidate, extract AUM via SECEdgarClient.
      4. Filter to AUM >= $100M (typical SFO threshold).
      5. Attempt HQ city extraction from SEC submissions address.
      6. Return SFOEntity-compatible seed dicts sorted by AUM descending.

    Args:
        max_candidates: Maximum number of entities to return (default 50).

    Returns:
        List of dicts compatible with SFOEntity seed format.
    """
    log = get_logger("discovery")
    sec = SECEdgarClient()

    # Phase 1: Search EFTS for ADV filings mentioning family office
    queries = [
        'form-type:"ADV" AND "family office"',
        'form-type:"ADV" AND "Regulatory Assets Under Management"',
        'form-type:"ADV" AND (LLC) AND "Regulatory Assets"',
    ]

    seen_ciks: set[str] = set()
    raw_records: list[dict] = []

    for q in queries:
        for start in range(0, max_candidates * 2, 100):
            data = _search_efts(q, start=start, page_size=100)
            if not data:
                break
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                break
            for hit in hits:
                rec = _extract_hit(hit)
                if rec and rec["cik"] not in seen_ciks:
                    seen_ciks.add(rec["cik"])
                    raw_records.append(rec)
            if len(hits) < 100:
                break

    log.log_api_call(
        "sec_efts", status=200,
        detail=f"Found {len(raw_records)} unique IA CIKs across {len(queries)} queries",
    )

    # Phase 2: Enrich with AUM via SECEdgarClient
    candidates: list[dict] = []

    for rec in raw_records:
        if len(candidates) >= max_candidates:
            break

        cik = rec["cik"]
        company_name = rec["company_name"]

        # Extract AUM using multi-strategy extractor
        aum = sec.extract_aum(entity_name=company_name, family_name=None)
        if aum is None or aum < 100_000_000:
            continue

        hq = _extract_hq_from_submissions(cik)
        hq_city = hq.split(",")[0].strip() if hq else None
        hq_country = "United States"

        is_sfo = _is_sfo_candidate(company_name)
        entity_type = "SFO" if is_sfo else "SFO"

        candidate = {
            "entity_name": company_name,
            "entity_type": entity_type,
            "family_name": None,
            "source_of_wealth": None,
            "estimated_aum_usd": aum,
            "aum_confidence": "Unresolved",
            "year_established": None,
            "website": None,
            "hq_city": hq_city,
            "hq_country": hq_country,
            "principals": [],
            "contacts": [
                {
                    "type": "email",
                    "value": "Unresolved",
                    "confidence": "Unresolved",
                    "notes": (
                        "Discovered via SEC IA bulk report. "
                        "Contact information pending enrichment."
                    ),
                }
            ],
            "enrichment_status": "pending",
        }
        candidates.append(candidate)

    # Phase 3: Sort by AUM descending
    candidates.sort(key=lambda x: x.get("estimated_aum_usd") or 0, reverse=True)

    # Phase 4: Assign sequential SFO IDs
    for i, c in enumerate(candidates, start=1):
        c["id"] = f"SFO-{i:03d}"

    log.log_api_call(
        "discovery", status=200,
        detail=f"Returning {len(candidates)} SFO candidates with AUM >= $100M",
    )

    return candidates


def run_discovery_bulk(
    min_aum: float = 100_000_000,
    max_results: int = 100,
) -> list[dict]:
    """Alias for run_discovery with explicit AUM threshold and result cap."""
    return run_discovery(max_candidates=max_results)
