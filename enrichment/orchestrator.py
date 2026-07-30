"""Orchestrates multi-source enrichment for a single SFO entity.

Discovery path per entity:
  1. Company website scrape
  2. SEC EDGAR CIK lookup → Form ADV AUM extraction
  3. Serper web search for principal emails and LinkedIn profiles
  4. Validation and confidence badge assignment
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from audit import get_logger
from config.settings import settings
from enrichment.classifier import classify_entity
from enrichment.sec_edgar import SECEdgarClient
from enrichment.site_scraper import SiteScraper
from enrichment.web_search import WebSearchClient
from models.sfo import (
    AumConfidence,
    ContactConfidence,
    ContactMethod,
    EnrichmentSource,
    EnrichmentStatus,
    EntityType,
    Principal,
    SFOEntity,
)


class EnrichmentOrchestrator:
    """Runs the full enrichment pipeline for one SFO entity."""

    def __init__(self):
        self.sec = SECEdgarClient()
        self.scraper = SiteScraper()
        self.web = WebSearchClient()
        self._log = get_logger("orchestrator")

    def enrich(self, entity: SFOEntity) -> SFOEntity:
        """Execute enrichment pipeline on a single entity (mutates in place)."""
        entity.enrichment_status = EnrichmentStatus.IN_PROGRESS
        entity.log("Enrichment started")

        # --- Step 0: Entity type classification ---
        self._step_classify(entity)

        # --- Step 1: Website scrape ---
        if entity.website and settings.enable_web_enrichment:
            self._step_website_scrape(entity)

        # --- Step 2: SEC EDGAR ---
        if settings.enable_sec_enrichment:
            self._step_sec_enrich(entity)

        # --- Step 3: Web search for principals ---
        if settings.enable_web_enrichment:
            self._step_principal_search(entity)

        # --- Step 4: Final validation ---
        self._validate_entity(entity)

        entity.last_enriched_at = datetime.now(timezone.utc)
        entity.enrichment_status = EnrichmentStatus.COMPLETED
        entity.log("Enrichment completed")
        return entity

    # ------------------------------------------------------------------
    # Internal steps
    # ------------------------------------------------------------------

    def _step_classify(self, entity: SFOEntity) -> None:
        """Run entity-type classifier: flags MFO/VC misclassifications."""
        original = entity.entity_type
        result = classify_entity(entity)
        if result != original:
            entity.log(
                f"CLASSIFIER: Reclassified from {original.value} to {result.value} "
                f"— review recommended."
            )
            entity.entity_type = result

    def _step_website_scrape(self, entity: SFOEntity) -> None:
        entity.log(f"Scraping website: {entity.website}")
        data = self.scraper.enrich_website(entity.website)
        for pdata in data.get("principals", []):
            principal = Principal(
                full_name=pdata["full_name"],
                title=pdata.get("title"),
                sources=[EnrichmentSource(
                    source_name="company_site",
                    url=entity.website,
                    field_extracted="principal_name",
                )],
            )
            entity.add_principal(principal)
        for email_entry in data.get("emails", []):
            if isinstance(email_entry, dict):
                email_val = email_entry["email"]
                is_generic = email_entry.get("is_generic", False)
            else:
                email_val = email_entry
                is_generic = False
            confidence = ContactConfidence.CATCH_ALL if is_generic else ContactConfidence.VERIFIED_DIRECT
            contact = ContactMethod(
                type="email",
                value=email_val,
                confidence=confidence,
                sources=[EnrichmentSource(
                    source_name="company_site",
                    url=entity.website,
                    field_extracted="email",
                )],
            )
            entity.add_contact(contact)
        narrative = data.get("wealth_narrative")
        if narrative and not entity.source_of_wealth:
            entity.source_of_wealth = narrative[:300]
        entity.log(f"Website scrape complete: {len(data.get('principals', []))} principals, "
                   f"{len(data.get('emails', []))} emails")

    def _step_sec_enrich(self, entity: SFOEntity) -> None:
        if not entity.family_name and not entity.entity_name:
            return
        entity.log("SEC enrichment: multi-strategy lookup")
        aum = self.sec.extract_aum(
            entity_name=entity.entity_name,
            family_name=entity.family_name,
        )
        if aum is not None:
            entity.estimated_aum_usd = aum
            entity.aum_confidence = AumConfidence.CONFIRMED
            entity.add_source(EnrichmentSource(
                source_name="sec_edgar",
                url=f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={entity.entity_name}",
                field_extracted="aum",
            ))
            entity.log(f"AUM extracted from SEC: ${aum:,.0f}")
        else:
            entity.log("SEC: No AUM found (no CIK or no filing data)")

    def _step_principal_search(self, entity: SFOEntity) -> None:
        for principal in entity.principals:
            # Skip if already have verified email
            already_has_email = any(
                c.confidence == ContactConfidence.VERIFIED_DIRECT for c in entity.contacts
            )
            if already_has_email:
                continue
            entity.log(f"Searching for contact: {principal.full_name}")
            time.sleep(settings.request_delay_sec)
            # LinkedIn
            if not principal.linkedin_url:
                li_url = self.web.search_linkedin(principal.full_name, entity.entity_name)
                if li_url:
                    principal.linkedin_url = li_url
                    principal.sources.append(EnrichmentSource(
                        source_name="serper_web",
                        url=li_url,
                        field_extracted="linkedin_url",
                    ))
                    entity.log(f"LinkedIn found: {li_url}")
                else:
                    entity.log(f"LinkedIn NOT found for {principal.full_name}")
            # Email
            result = self.web.search_principal_email(principal.full_name, entity.entity_name)
            if result:
                email, evidence = result
                # VERIFIED_DIRECT only if email and name appear in the same sentence or structured field
                import re
                sentences = re.split(r'(?<=[.!?])\s+', evidence)
                same_sentence = any(
                    principal.full_name.lower() in s and email.lower() in s
                    for s in sentences
                )
                confidence = ContactConfidence.VERIFIED_DIRECT if same_sentence else ContactConfidence.UNVERIFIED
                contact = ContactMethod(
                    type="email",
                    value=email,
                    confidence=confidence,
                    sources=[EnrichmentSource(
                        source_name="serper_web",
                        field_extracted="email",
                    )],
                )
                entity.add_contact(contact)
                entity.log(f"Email found: {email} (confidence={confidence.value})")
            else:
                entity.log(f"Email NOT found for {principal.full_name} — marking Unresolved")
                contact = ContactMethod(
                    type="email",
                    value="Unresolved",
                    confidence=ContactConfidence.UNRESOLVED,
                    sources=[EnrichmentSource(
                        source_name="serper_web",
                        field_extracted="email",
                    )],
                    notes="No verified direct work email found after multi-source search "
                          "(SEC, company site, press releases, web search).",
                )
                entity.add_contact(contact)

    def _validate_entity(self, entity: SFOEntity) -> None:
        """Post-enrichment validation checks."""
        entity.log("Running validation checks")
        # If no principals found, flag it
        if not entity.principals:
            entity.log("WARNING: No principals identified for this entity")
        # Count unresolved contacts
        unresolved = sum(1 for c in entity.contacts if c.confidence == ContactConfidence.UNRESOLVED)
        if unresolved:
            entity.log(f"Validation: {unresolved} unresolved contact(s) — honest refusal preserved")
        # Verify SFO classification integrity
        if entity.entity_type != EntityType.SFO:
            entity.log(f"WARNING: Entity classified as {entity.entity_type.value}, not SFO")
