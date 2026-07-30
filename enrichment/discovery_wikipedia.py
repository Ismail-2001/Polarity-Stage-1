"""Wikipedia-based SFO discovery — independent source of family office names.

Queries the Wikipedia API for the "Family office" page and parses the
"Select list of family offices" section for bulleted entity names.

This is an independent source from the SEC EFTS search, satisfying the
multi-source discovery requirement (principle 4).
"""

from __future__ import annotations

import re
from typing import Optional

import requests

from audit import get_logger
from models.sfo import AumConfidence

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
PAGE_NAME = "Family office"
SECTION_NAME = "Select list of family offices"

HEADERS = {
    "User-Agent": "FamilyOfficePipeline/1.0 (research; Wikipedia list parsing)",
    "Accept": "application/json",
}

# Entities that appear in the section but are not family offices
EXCLUDED_ENTITIES = {
    "private foundation", "trust fund",
}


def _fetch_wikitext(page_title: str) -> Optional[str]:
    """Fetch raw wikitext for a given Wikipedia page."""
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "wikitext",
        "format": "json",
        "formatversion": "2",
    }
    try:
        resp = requests.get(WIKIPEDIA_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log = get_logger("discovery_wikipedia")
            log.log_failure("wikipedia_api", error=data["error"].get("info", str(data["error"])), entity=page_title)
            return None
        wt = data.get("parse", {}).get("wikitext")
        if isinstance(wt, dict):
            wt = wt.get("*")
        return wt
    except (requests.RequestException, KeyError, ValueError) as e:
        log = get_logger("discovery_wikipedia")
        log.log_failure("wikipedia_api", error=str(e), entity=page_title)
        return None


def _parse_bullet_list(wikitext: str, section_title: str) -> list[str]:
    """Parse bullet items under a given section title.

    Returns a list of entity names extracted from wiki-links.
    """
    lines = wikitext.split("\n")
    in_section = False
    entities: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Detect section start
        if stripped.startswith("==") and section_title.lower() in stripped.lower():
            in_section = True
            continue

        # Detect next section
        if in_section and stripped.startswith("==") and section_title.lower() not in stripped.lower():
            break

        if not in_section:
            continue

        # Bullet line with wiki link
        if stripped.startswith("*") and "[[" in stripped:
            # Extract the display text from [[Page|Display]] or just [[Page]]
            links = re.findall(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", stripped)
            for page, display in links:
                name = (display or page).strip()
                if name.lower() in EXCLUDED_ENTITIES:
                    continue
                if len(name) >= 3:
                    entities.append(name)

    return entities


def run_wikipedia_discovery(max_candidates: int = 50) -> list[dict]:
    """Discover SFO candidates from Wikipedia's ''Family office'' page.

    Returns a list of SFOEntity-compatible seed dicts.
    """
    log = get_logger("discovery_wikipedia")
    log.log_api_call("wikipedia_api", status=200, detail=f"Fetching page: {PAGE_NAME}")

    wikitext = _fetch_wikitext(PAGE_NAME)
    if not wikitext:
        log.log_failure("wikipedia_api", error="Failed to fetch wikitext", entity=PAGE_NAME)
        return []

    names = _parse_bullet_list(wikitext, SECTION_NAME)
    log.log_api_call("wikipedia_api", status=200, detail=f"Parsed {len(names)} entity names from section")

    candidates: list[dict] = []
    for name in names:
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
            "discovery_source": "wikipedia",
            "principals": [],
            "contacts": [
                {
                    "type": "email",
                    "value": "Unresolved",
                    "confidence": "Unresolved",
                    "notes": "Discovered via Wikipedia. Enrichment pending.",
                }
            ],
            "enrichment_status": "pending",
        })

    candidates.sort(key=lambda x: x.get("entity_name", ""))
    for i, c in enumerate(candidates, start=1):
        c["id"] = f"SFO-WIKI-{i:03d}"

    log.log_api_call("discovery_wikipedia", status=200, detail=f"Returning {len(candidates)} SFO candidates")
    return candidates


def run_multi_source_discovery(max_candidates: int = 50) -> list[dict]:
    """Run discovery from all available sources and merge results.

    Sources (in order):
      - SEC EFTS (enrichment.discovery.run_discovery)
      - Wikipedia select list (this module)
      - Web directory scrape (enrichment.discovery_directory.run_directory_discovery)

    Deduplicates by normalized entity name (case-insensitive).
    Computes and logs source distribution to verify no single source >50%.
    """
    from enrichment.discovery import run_discovery as _run_sec_discovery
    from enrichment.discovery_directory import run_directory_discovery

    log = get_logger("multi_source_discovery")
    all_candidates: list[dict] = []
    seen_normalized: set[str] = set()
    source_counts: dict[str, int] = {}

    sources = [
        ("sec_efts", _run_sec_discovery),
        ("wikipedia", run_wikipedia_discovery),
        ("web_directory", run_directory_discovery),
    ]

    for source_name, discover_fn in sources:
        try:
            results = discover_fn(max_candidates=max_candidates)
            for cand in results:
                norm = cand.get("entity_name", "").lower().strip()
                if norm and norm not in seen_normalized:
                    seen_normalized.add(norm)
                    cand["discovery_source"] = source_name
                    all_candidates.append(cand)
                    source_counts[source_name] = source_counts.get(source_name, 0) + 1
            log.log_api_call(source_name, status=200, detail=f"Source returned {len(results)} candidates")
        except Exception as e:
            log.log_failure(source_name, error=str(e), entity="multi_source")

    all_candidates.sort(key=lambda x: x.get("estimated_aum_usd") or 0, reverse=True)
    for i, c in enumerate(all_candidates, start=1):
        c["id"] = f"SFO-{i:03d}"

    # Log source distribution
    total = len(all_candidates)
    dist_str = "; ".join(
        f"{src}={count} ({count / total * 100:.1f}%)"
        for src, count in sorted(source_counts.items(), key=lambda x: -x[1])
    )
    log.log_api_call("multi_source_distribution", status=200, detail=dist_str)
    log.log_api_call("multi_source_discovery", status=200, detail=f"Total {total} candidates from {len(sources)} sources")

    # Warn if any source dominates
    for src, count in source_counts.items():
        if count / total > 0.5:
            log.log_failure("multi_source_imbalance", error=f"{src} dominates at {count / total * 100:.1f}%", entity="multi_source")

    return all_candidates
