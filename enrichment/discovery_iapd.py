"""SEC Investment Adviser Public Disclosure (IAPD) discovery — third independent channel.

Queries the SEC IAPD / ADV registrant search API for advisers whose
business names contain family-office-related terms. This is a different
SEC dataset from the EFTS filing-text search (which searches filing
content), whereas IAPD searches the registered investment adviser database.

Independent of: SEC EFTS (different dataset/different API endpoint),
Wikipedia (different data source entirely).
"""

from __future__ import annotations

import re

import requests

from audit import get_logger
from models.sfo import AumConfidence

IAPD_SEARCH_URL = "https://adviserinfo.sec.gov/iapd/IAPDAdvisers/SearchAdvisers"
IAPD_HEADERS = {
    "User-Agent": "FamilyOfficePipeline/1.0 (research; IAPD discovery)",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

SEARCH_TERMS = [
    "family office",
    "family office investments",
    "single family office",
]

# Forms we care about — family offices typically file Form ADV
ADV_FORMS = {"ADV", "ADV-E", "ADV-NR"}


def _search_iapd(query: str, page_size: int = 100) -> list[dict]:
    """Search the SEC IAPD adviser database for a given query string."""
    log = get_logger("discovery_iapd")
    params = {
        "searchTerm": query,
        "page": 1,
        "pageSize": page_size,
    }
    try:
        resp = requests.get(
            IAPD_SEARCH_URL,
            params=params,
            headers=IAPD_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("rows", []) if isinstance(data, dict) else data
        log.log_api_call("iapd_search", status=resp.status_code, detail=f"Query {query!r}: {len(results)} results")
        return results
    except requests.RequestException as e:
        log.log_failure("iapd_search", error=str(e), entity=query)
        return []


def run_iapd_discovery(max_candidates: int = 50) -> list[dict]:
    """Discover SFO candidates via SEC IAPD registered adviser search.

    Searches for investment advisers with family-office-related business
    names, which is a distinct dataset from the EFTS filing-text search.

    Returns SFOEntity-compatible dicts tagged with discovery_source='sec_iapd'.
    """
    log = get_logger("discovery_iapd")
    seen_names: set[str] = set()
    all_candidates: list[dict] = []

    for term in SEARCH_TERMS:
        results = _search_iapd(term)
        for row in results:
            if len(all_candidates) >= max_candidates:
                break

            legal_name = (row.get("legalName") or "").strip()
            main_name = (row.get("mainName") or "").strip()
            biz_name = (row.get("businessName") or "").strip()

            name = legal_name or main_name or biz_name
            if not name:
                continue

            # Normalize: clean up punctuation, excess whitespace
            name = re.sub(r"\s+", " ", name).strip()
            low = name.lower()

            if low in seen_names:
                continue
            seen_names.add(low)

            # Reject candidates that are clearly not family offices
            if _excluded_name(name):
                continue

            crd = row.get("crdNumber") or ""
            hq = row.get("mainOfficeCity") or row.get("city") or ""
            state = row.get("mainOfficeState") or ""
            hq_str = f"{hq}, {state}" if hq and state else hq or None

            all_candidates.append({
                "entity_name": name,
                "entity_type": "SFO",
                "family_name": None,
                "source_of_wealth": None,
                "estimated_aum_usd": None,
                "aum_confidence": AumConfidence.UNRESOLVED.value,
                "year_established": None,
                "website": None,
                "hq_city": hq_str,
                "hq_country": "United States",
                "discovery_source": "sec_iapd",
                "principals": [],
                "contacts": [
                    {
                        "type": "email",
                        "value": "Unresolved",
                        "confidence": "Unresolved",
                        "notes": f"Discovered via SEC IAPD (CRD: {crd}). Enrichment pending.",
                    }
                ],
                "enrichment_status": "pending",
            })

    all_candidates.sort(key=lambda x: x.get("entity_name", ""))
    for i, c in enumerate(all_candidates, start=1):
        c["id"] = f"SFO-IAPD-{i:03d}"

    log.log_api_call("discovery_iapd", status=200, detail=f"Returning {len(all_candidates)} SFO candidates from IAPD")
    return all_candidates


def _excluded_name(name: str) -> bool:
    """Reject candidates whose names indicate they are not family offices."""
    low = name.lower()
    excludes = [
        "etf", "mutual fund", "index fund", "insurance company",
        "bank", "trust company", "credit union", " brokerage",
        "registered investment adviser", "ria", "ria ",
        "securities", "exchange", "clearing",
    ]
    return any(ex in low for ex in excludes)
