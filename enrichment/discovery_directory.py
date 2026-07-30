"""Web-scraped SFO discovery from publicly available family office directories.

Fetches and parses known family office directory pages from the web.
This is independent from SEC EFTS and Wikipedia since it uses different
source URLs and extraction methods.

Currently uses:
  - Wikipedia Category:Family_offices members (authoritative sub-category)
"""

from __future__ import annotations

import re
from typing import Optional

import requests

from audit import get_logger
from models.sfo import AumConfidence

WIKI_API = "https://en.wikipedia.org/w/api.php"
HEADERS = {
    "User-Agent": "FamilyOfficePipeline/1.0 (research; web directory discovery)",
    "Accept": "application/json",
}

EXCLUDED_NAMES = {
    "private foundation", "trust fund", "family office", "investment management",
    "financial institution", "estate planning", "assets under management",
    "subsidiary", "division",
}

SFO_KEYWORDS = {
    "group", "management", "capital", "partners", "holdings", "investments",
    "enterprises", "associates", "advisors", "company", "incorporated",
}


def _fetch_wikipedia_category(category: str = "Family_offices") -> list[str]:
    """Fetch page titles in a Wikipedia category."""
    log = get_logger("discovery_directory")
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": "max",
        "format": "json",
        "formatversion": "2",
    }
    try:
        resp = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        members = data.get("query", {}).get("categorymembers", [])
        names = [m["title"] for m in members if ":" not in m.get("title", "")]
        log.log_api_call("wikipedia_category", status=200, detail=f"Found {len(names)} members in Category:{category}")
        return names
    except (requests.RequestException, KeyError, ValueError) as e:
        log.log_failure("wikipedia_category", error=str(e), entity=category)
        return []


def _is_sfo_name(name: str) -> bool:
    """Check if a name looks like a real SFO entity."""
    low = name.lower()
    if low in EXCLUDED_NAMES:
        return False
    if len(name.split()) < 2:
        return False
    has_sfo_keyword = any(kw in low for kw in SFO_KEYWORDS)
    if not has_sfo_keyword:
        return False
    return True


def run_directory_discovery(max_candidates: int = 50) -> list[dict]:
    """Discover SFO candidates from public web directories.

    Combines results from multiple directory sources, deduplicated.
    """
    log = get_logger("discovery_directory")
    all_names: list[str] = []
    seen: set[str] = set()

    wiki_names = _fetch_wikipedia_category("Family_offices")
    for name in wiki_names:
        clean = re.sub(r"\s*\(.*?\)\s*", "", name).strip()
        if not _is_sfo_name(clean):
            continue
        low = clean.lower()
        if low not in seen and len(clean) >= 5:
            seen.add(low)
            all_names.append(clean)

    log.log_api_call("discovery_directory", status=200, detail=f"Total {len(all_names)} unique names from directories")

    candidates: list[dict] = []
    for name in all_names:
        if len(candidates) >= max_candidates:
            break
        candidates.append({
            "entity_name": name,
            "entity_type": "SFO",
            "family_name": None,
            "source_of_wealth": None,
            "estimated_aum_usd": None,
            "aum_confidence": AumConfidence.UNRESOLVED.value,
            "year_established": None,
            "website": None,
            "hq_city": None,
            "hq_country": None,
            "discovery_source": "web_directory",
            "principals": [],
            "contacts": [
                {
                    "type": "email",
                    "value": "Unresolved",
                    "confidence": "Unresolved",
                    "notes": "Discovered via web directory listing. Enrichment pending.",
                }
            ],
            "enrichment_status": "pending",
        })

    for i, c in enumerate(candidates, start=1):
        c["id"] = f"SFO-DIR-{i:03d}"

    log.log_api_call("discovery_directory", status=200, detail=f"Returning {len(candidates)} SFO candidates")
    return candidates
