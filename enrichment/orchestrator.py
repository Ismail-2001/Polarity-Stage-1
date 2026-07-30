"""Orchestrates multi-source enrichment for a single SFO entity.

Discovery path per entity:
  0. Entity type classification
  0a. Wikipedia website discovery
  0b. Manual AUM override (from data/manual_aum.json)
  1. Company website scrape (link-based team page discovery)
  2. SEC EDGAR CIK lookup → Form ADV AUM extraction
  3. Serper web search + Hunter.io fallback for principal emails
  4. Validation and confidence badge assignment
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from audit import get_logger
from config.settings import settings
from enrichment.classifier import classify_entity
from enrichment.discovery_wikipedia import _extract_wikipedia_website
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
        self._overrides = self._load_overrides()

    def _load_overrides(self) -> dict:
        """Load comprehensive manual overrides from data/manual_overrides.json."""
        path = settings.resolved_data_dir / "manual_overrides.json"
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return {}

    def enrich(self, entity: SFOEntity) -> SFOEntity:
        """Execute enrichment pipeline on a single entity (mutates in place)."""
        entity.enrichment_status = EnrichmentStatus.IN_PROGRESS
        entity.log("Enrichment started")

        # --- Step 0: Entity type classification ---
        self._step_classify(entity)

        # --- Step 0a: Manual overrides (AUM, principals, emails, etc.) ---
        self._step_apply_overrides(entity)

        # --- Step 0b: Wikipedia website discovery (if still missing) ---
        self._step_wikipedia_website(entity)

        # --- Step 1: Website scrape (if website found and overrides didn't provide principals) ---
        if entity.website and settings.enable_web_enrichment and not entity.principals:
            self._step_website_scrape(entity)

        # --- Step 2: SEC EDGAR (AUM extraction if still missing) ---
        if settings.enable_sec_enrichment and entity.estimated_aum_usd is None:
            self._step_sec_enrich(entity)

        # --- Step 3: Web search for principals (if still missing) ---
        if settings.enable_web_enrichment and not entity.principals:
            self._step_principal_search(entity)

        # --- Step 4: Final validation ---
        self._validate_entity(entity)

        entity.last_enriched_at = datetime.now(timezone.utc)
        entity.last_verified_at = datetime.now(timezone.utc)
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

    def _step_wikipedia_website(self, entity: SFOEntity) -> None:
        if entity.website:
            return
        url = _extract_wikipedia_website(entity.entity_name)
        if url:
            entity.website = url
            entity.add_source(EnrichmentSource(
                source_name="wikipedia",
                field_extracted="website",
                url=f"https://en.wikipedia.org/wiki/{entity.entity_name.replace(' ', '_')}",
            ))
            entity.log(f"Website discovered via Wikipedia: {url}")

    def _step_apply_overrides(self, entity: SFOEntity) -> None:
        """Apply all manual overrides from data/manual_overrides.json."""
        name = entity.entity_name
        overrides = self._overrides

        # --- AUM ---
        if entity.estimated_aum_usd is None:
            aum_data = overrides.get("aum", {}).get(name)
            if aum_data:
                entity.estimated_aum_usd = aum_data["aum_usd"]
                entity.aum_confidence = AumConfidence(aum_data.get("confidence", "Confirmed"))
                entity.add_source(EnrichmentSource(
                    source_name="manual_override",
                    field_extracted="aum",
                    raw_snippet=aum_data.get("source", ""),
                ))
                entity.log(f"Manual AUM override: ${entity.estimated_aum_usd:,.0f}")

        # --- Website ---
        if not entity.website:
            website = overrides.get("websites", {}).get(name)
            if website:
                entity.website = website
                entity.add_source(EnrichmentSource(
                    source_name="manual_override",
                    field_extracted="website",
                    url=website,
                ))
                entity.log(f"Manual website override: {website}")

        # --- Principals ---
        if not entity.principals:
            principals_data = overrides.get("principals", {}).get(name, [])
            for pdata in principals_data:
                principal = Principal(
                    full_name=pdata["full_name"],
                    title=pdata.get("title"),
                    sources=[EnrichmentSource(
                        source_name="manual_override",
                        field_extracted="principal_name",
                    )],
                )
                entity.add_principal(principal)
            if principals_data:
                entity.log(f"Manual principals override: {len(principals_data)} principals added")

        # --- Emails ---
        has_verified_email = any(
            c.confidence in (ContactConfidence.VERIFIED_DIRECT, ContactConfidence.CATCH_ALL)
            for c in entity.contacts
        )
        if not has_verified_email:
            email_data = overrides.get("emails", {}).get(name)
            if email_data:
                confidence_str = email_data.get("confidence", "Catch-all / Generic Inbox")
                try:
                    confidence = ContactConfidence(confidence_str)
                except ValueError:
                    confidence = ContactConfidence.CATCH_ALL
                contact = ContactMethod(
                    type="email",
                    value=email_data["email"],
                    confidence=confidence,
                    sources=[EnrichmentSource(
                        source_name="manual_override",
                        field_extracted="email",
                        raw_snippet=email_data.get("source", ""),
                    )],
                )
                entity.add_contact(contact)
                entity.log(f"Manual email override: {email_data['email']}")

        # --- Source of wealth ---
        if not entity.source_of_wealth:
            sow = overrides.get("source_of_wealth", {}).get(name)
            if sow:
                entity.source_of_wealth = sow
                entity.add_source(EnrichmentSource(
                    source_name="manual_override",
                    field_extracted="source_of_wealth",
                ))
                entity.log("Manual source_of_wealth override")

        # --- Year established ---
        if not entity.year_established:
            year = overrides.get("year_established", {}).get(name)
            if year:
                entity.year_established = year
                entity.add_source(EnrichmentSource(
                    source_name="manual_override",
                    field_extracted="year_established",
                ))
                entity.log(f"Manual year_established override: {year}")

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
        principal_names = [p.full_name.lower() for p in entity.principals]
        for email_entry in data.get("emails", []):
            if isinstance(email_entry, dict):
                email_val = email_entry["email"]
                is_generic = email_entry.get("is_generic", False)
            else:
                email_val = email_entry
                is_generic = False
            email_local = email_val.split("@")[0].lower().replace(".", "").replace("_", "").replace("-", "")
            name_tied = any(email_local in pn.replace(" ", "") for pn in principal_names)
            confidence = ContactConfidence.CATCH_ALL if (is_generic or not name_tied) else ContactConfidence.VERIFIED_DIRECT
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
        entity.log("SEC enrichment: multi-strategy lookup")
        cik = entity.cik
        if not cik:
            entity.log("SEC: No CIK carried from discovery — trying name-based resolution")
            if not entity.family_name and not entity.entity_name:
                return
            cik = self.sec.lookup_cik(self.sec.generate_name_variants(
                entity.family_name or "", entity.entity_name,
            ))
        if cik:
            aum = self.sec._extract_aum_from_facts(cik) or self.sec.extract_aum_from_adv(cik) or self.sec._extract_aum_from_13f(cik)
        else:
            aum = None
        if aum is not None:
            entity.estimated_aum_usd = aum
            entity.aum_confidence = AumConfidence.CONFIRMED
            entity.cik = cik or entity.cik
            entity.add_source(EnrichmentSource(
                source_name="sec_edgar",
                url=f"https://www.sec.gov/cgi-bin/browse-edgar?CIK={cik}",
                field_extracted="aum",
            ))
            entity.log(f"AUM extracted from SEC: ${aum:,.0f}")
        elif cik:
            entity.log(f"SEC: CIK {cik} found but AUM extraction returned None")
        else:
            entity.log("SEC: No CIK found — cannot extract AUM")

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
