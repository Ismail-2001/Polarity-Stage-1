"""Serper web-search-based SFO discovery — third independent discovery channel.

Queries Serper.dev for family office directories and listings, then
extracts candidate entity names from search result titles and snippets.

This is independent from SEC EFTS (filing text search) and Wikipedia
(manually curated list), satisfying the multi-source diversity requirement.
"""

from __future__ import annotations

import re

from audit import get_logger
from enrichment.web_search import WebSearchClient
from models.sfo import AumConfidence

# Queries designed to surface family office directories/listings rather
# than news articles or academic definitions
DISCOVERY_QUERIES = [
    "single family office directory list",
    "family office database USA",
    "family office firms United States",
    "top family offices America",
    "family office investment firm list",
    "notable family offices United States",
    "registered family offices SEC",
    "family office wealth management firms",
]

# Generic phrases that appear in search results but aren't entity names
SKIP_TITLES = {
    "family office", "single family office", "multi family office",
    "what is a family office", "family office directory", "family office list",
    "home", "contact", "about us", "services", "login", "sign in",
    "family office association", "family office network",
    "top 10", "top 50", "top 100", "the top",
}


def _extract_entity_names(results: list[dict]) -> list[str]:
    """Parse candidate entity names from Serper search result titles/snippets.

    Uses heuristics to extract company-like names while avoiding generic text.
    """
    names: list[str] = []
    seen: set[str] = set()

    for r in results:
        title = r.get("title", "").strip()
        snippet = r.get("snippet", "").strip()

        # Combine title and snippet, split on common separators
        text = f"{title} - {snippet}"

        # Find capitalized multi-word phrases (potential entity names)
        candidates = re.findall(
            r"(?:^|[|\-–—•·•·•·])\s*([A-Z][A-Za-z.&]+(?:\s+[A-Z][A-Za-z.&]+)+(?:\s+(?:Inc|LLC|LP|Corp|Group|Partners|Management|Capital|Advisors|Investments|Associates|LLP|Ltd|Limited|Company|Co|Trust))?\.?)",
            text,
        )
        for c in candidates:
            c = c.strip().strip(".,;:")
            low = c.lower().strip()
            if low in SKIP_TITLES:
                continue
            if len(c) < 5:
                continue
            if c in seen:
                continue
            seen.add(c)
            names.append(c)

    return names


def run_serper_discovery(max_candidates: int = 50) -> list[dict]:
    """Discover SFO candidates via Serper web search.

    Queries multiple family-office-related search terms, extracts entity
    names from result titles/snippets, and returns SFOEntity-compatible dicts.

    Returns:
        List of seed dicts tagged with discovery_source='serper_web'.
    """
    log = get_logger("discovery_serper")
    client = WebSearchClient()
    all_names: list[str] = []
    seen_names: set[str] = set()

    for query in DISCOVERY_QUERIES:
        log.log_api_call("serper_web", status=0, detail=f"Searching: {query!r}")
        results = client.search(query, num=10)
        names = _extract_entity_names(results)
        for name in names:
            low = name.lower()
            if low not in seen_names:
                seen_names.add(low)
                all_names.append(name)
        log.log_api_call("serper_web", status=200, detail=f"Query {query!r}: {len(names)} names extracted")

    log.log_api_call("discovery_serper", status=200, detail=f"Total {len(all_names)} unique names across {len(DISCOVERY_QUERIES)} queries")

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
            "discovery_source": "serper_web",
            "principals": [],
            "contacts": [
                {
                    "type": "email",
                    "value": "Unresolved",
                    "confidence": "Unresolved",
                    "notes": "Discovered via Serper web search. Enrichment pending.",
                }
            ],
            "enrichment_status": "pending",
        })

    for i, c in enumerate(candidates, start=1):
        c["id"] = f"SFO-SRP-{i:03d}"

    log.log_api_call("discovery_serper", status=200, detail=f"Returning {len(candidates)} SFO candidates")
    return candidates
