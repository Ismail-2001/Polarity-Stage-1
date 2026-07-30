"""SEC EDGAR IA bulk discovery — replaces the fabricated 50-entry seed list.

Queries the SEC Elasticsearch Full-Text Search (EFTS) API for filings that
mention "family office", extracts unique CIKs with family-office-like names,
and enriches each candidate with HQ city and AUM via the existing client.

The EFTS response stores ciks and display_names as parallel arrays; a single
filing may list multiple filers (issuer + family office filing jointly).
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import urlencode

import requests

from audit import get_logger
from enrichment.sec_edgar import SECEdgarClient, SEC_SUBMISSIONS
from models.sfo import AumConfidence

SEC_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"

SEC_HEADERS = {
    "User-Agent": "FamilyOfficePipeline/1.0 (research; EFTS CIK discovery)",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
}

FAMILY_OFFICE_PATTERNS = [
    re.compile(r"\bfamily\s*office\b", re.IGNORECASE),
    re.compile(r"\bEMFO\b", re.IGNORECASE),
    re.compile(r"\bSFO\b", re.IGNORECASE),
    re.compile(r"\bFO[\s,.]", re.IGNORECASE),
    re.compile(r"\bsingle\s*family\b", re.IGNORECASE),
    re.compile(r"\bmulti[- ]family\b", re.IGNORECASE),
    re.compile(r"\bfamily\s*investment\b", re.IGNORECASE),
    re.compile(r"\bfamily\s+(?:partners|capital|holdings|group|llc|lp|inc\.?)", re.IGNORECASE),
]

EXCLUDE_PATTERNS = [
    re.compile(r"\bETF\b", re.IGNORECASE),
    re.compile(r"Mutual Fund", re.IGNORECASE),
    re.compile(r"Index Fund", re.IGNORECASE),
    re.compile(r"(?i)institute\b"),
    re.compile(r"(?i)of america\b"),
    re.compile(r"(?i)bank\b"),
    re.compile(r"(?i)trust co\b"),
]


def _rate_limit() -> None:
    time.sleep(0.15)


def _search_efts(query: str, start: int = 0, page_size: int = 100) -> Optional[dict]:
    params = {"q": query, "dateRange": "all", "start": str(start), "counts": str(min(page_size, 100))}
    url = f"{SEC_EFTS_URL}?{urlencode(params)}"
    _rate_limit()
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return None


def _extract_hits(hit: dict) -> list[dict]:
    """Extract all {cik, display_name, biz_location} from a single EFTS hit.

    ciks and display_names are parallel arrays that may hold multiple filers
    for a single filing (e.g. issuer + family office 13G joint filers).
    """
    src = hit.get("_source", {})
    ciks = src.get("ciks") or []
    names = src.get("display_names") or []
    biz = src.get("biz_locations") or []
    bz = biz[0].strip() if biz else None
    out = []
    for i in range(len(ciks)):
        cik = str(ciks[i]).zfill(10)
        name = names[i].strip() if i < len(names) else ""
        if cik and name:
            out.append({"cik": cik, "display_name": name, "biz_location": bz})
    return out


def _parse_display_name(raw: str) -> str:
    """Strip the trailing '  (CIK ##########)' suffix."""
    m = re.match(r"^(.+?)\s{2,}\(CIK \d+\)", raw)
    return m.group(1).strip() if m else raw.strip()


def _is_sfo_name(name: str) -> bool:
    if not name:
        return False
    for p in EXCLUDE_PATTERNS:
        if p.search(name):
            return False
    for p in FAMILY_OFFICE_PATTERNS:
        if p.search(name):
            return True
    return False


def _try_extract_aum(sec: SECEdgarClient, cik: str, entity_name: str) -> Optional[float]:
    """Multi-strategy AUM extraction for a known CIK, returns None on failure."""
    try:
        aum = sec._extract_aum_from_facts(cik)
        if aum is not None:
            return aum
    except Exception:
        pass
    try:
        aum = sec.extract_aum_from_adv(cik)
        if aum is not None:
            return aum
    except Exception:
        pass
    try:
        aum = sec._extract_aum_from_13f(cik)
        if aum is not None:
            return aum
    except Exception:
        pass
    return None


def _extract_hq_from_submissions(cik: str) -> Optional[str]:
    """Business city/state from SEC submissions address data."""
    url = SEC_SUBMISSIONS.format(cik)
    _rate_limit()
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        bus = data.get("addresses", {}).get("business", {})
        city = bus.get("city", "") or ""
        state = bus.get("stateOrCountry", "") or ""
        return f"{city}, {state}".strip(", ") if city else None
    except (requests.RequestException, KeyError, ValueError):
        return None


def _submissions_name(cik: str) -> Optional[str]:
    """Extract the entity name from SEC submissions data (authoritative source)."""
    url = SEC_SUBMISSIONS.format(cik)
    _rate_limit()
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("name") or "").strip() or None
    except (requests.RequestException, KeyError, ValueError):
        return None


def run_discovery(max_candidates: int = 50) -> list[dict]:
    """Discover SFO candidates from SEC EDGAR filings data.

    Pipeline:
      1. Search EFTS for "family office" across all filing forms.
      2. Deduplicate by CIK; keep only entities with family-office-like names.
      3. Retrieve submissions name (authoritative) and HQ city.
      4. Attempt AUM extraction via XBRL facts / ADV HTML / 13F.
      5. Return SFOEntity-compatible seed dicts sorted by AUM descending.

    Args:
        max_candidates: Maximum entities to return (default 50).

    Returns:
        List of SFOEntity-compatible seed dicts.
    """
    log = get_logger("discovery")
    sec = SECEdgarClient()

    # Phase 1 — EFTS search across multiple query patterns
    seen: set[str] = set()
    raw: list[dict] = []
    queries = [
        '"family office"',
        '"Family Office" LLC',
        '"Family Office" LP',
        '"FO" "family" AND 13F',
        '"family office" AND investments',
        '"family office" AND management',
        '"family office" AND "wealth"',
        '"SFO" LLC -"etf" -"mutual"',
        '"Family Office" fund',
        '"family office" LP -"fund"',
        '"family partners" AND "13F"',
        '"family capital" AND "13F"',
        '"family holdings" "office"',
    ]
    for q in queries:
        for start in range(0, 1000, 100):
            data = _search_efts(q, start=start, page_size=100)
            if not data:
                break
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                break
            for hit in hits:
                for rec in _extract_hits(hit):
                    if rec["cik"] not in seen:
                        seen.add(rec["cik"])
                        raw.append(rec)
            if len(hits) < 100:
                break
    log.log_api_call("sec_efts", status=200, detail=f"Found {len(raw)} unique CIKs across {len(queries)} queries")

    # Phase 2 — filter to SFO-like names, enrich
    candidates: list[dict] = []
    processed: set[str] = set()

    for rec in raw:
        if len(candidates) >= max_candidates:
            break

        cik = rec["cik"]
        clean_name = _parse_display_name(rec["display_name"])
        if not _is_sfo_name(clean_name):
            continue
        if cik in processed:
            continue
        processed.add(cik)

        # Authoritative name from submissions data
        auth_name = _submissions_name(cik) or clean_name
        hq = _extract_hq_from_submissions(cik) or rec.get("biz_location")
        aum = _try_extract_aum(sec, cik, auth_name)

        hq_city = hq.split(",")[0].strip() if hq else None

        candidates.append({
            "entity_name": auth_name,
            "entity_type": "SFO",
            "family_name": None,
            "source_of_wealth": None,
            "estimated_aum_usd": aum,
            "aum_confidence": AumConfidence.UNRESOLVED.value,
            "year_established": None,
            "website": None,
            "hq_city": hq_city,
            "hq_country": "United States",
            "discovery_source": "sec_efts",
            "principals": [],
            "contacts": [
                {
                    "type": "email",
                    "value": "Unresolved",
                    "confidence": "Unresolved",
                    "notes": "Discovered via SEC EDGAR bulk IA search. Enrichment pending.",
                }
            ],
            "enrichment_status": "pending",
        })

    candidates.sort(key=lambda x: x.get("estimated_aum_usd") or 0, reverse=True)
    for i, c in enumerate(candidates, start=1):
        c["id"] = f"SFO-{i:03d}"

    log.log_api_call("discovery", status=200, detail=f"Returning {len(candidates)} SFO candidates")
    return candidates
