"""Entity-type classifier that discriminates SFO vs MFO vs VC.

Applied during enrichment to detect and reject or reclassify non-SFO entities.

Rules:
  1. If website or marketing copy says "multi-family office" → MFO
  2. If entity explicitly serves external clients → MFO
  3. If entity is a registered investment adviser with >15 clients → MFO
  4. If entity name contains "Ventures", "Capital", "Partners" and
     describes raising external capital → VC
  5. If entity name contains "REIT", "Fund", "Trust" in non-family context → exclude
  6. Otherwise, default to SFO classification
"""

from __future__ import annotations

import re

from models.sfo import EntityType, SFOEntity

# ── Keywords that trigger reclassification ───────────────────────────────────

MFO_TRIGGERS = re.compile(
    r"\b(multi-family\s*office|multi family office|"
    r"serve.*(multiple|several|many).*famil(y|ies)|"
    r"open.*(to|for).*external|"
    r"services.*(to|for).*famil(y|ies)|"
    r"wealth management.*clients|"
    r"registered investment adviser)\b",
    re.IGNORECASE,
)

VC_TRIGGERS = re.compile(
    r"\b(venture capital|venture fund|early-stage|series [a-d]|"
    r"raise.*fund|institutional.*lp|limited partner|"
    r"portfolio company|startup investment)\b",
    re.IGNORECASE,
)

MFO_NAME_HINTS = re.compile(
    r"\b(multi[-\s]?family|family\s*wealth\s*management|"
    r"family office services|consolidated.*family)\b",
    re.IGNORECASE,
)

VC_NAME_HINTS = re.compile(
    r"\b(ventures|capital\s+partners|equity\s+partners|"
    r"growth\s+equity|venture\s+partners|angel\s+fund)\b",
    re.IGNORECASE,
)

# ── Name patterns that are NOT true SFOs ─────────────────────────────────────

NON_SFO_NAME_PATTERNS = re.compile(
    r"\b("
    r"reit|real\s+estate\s+investment\s+trust|"
    r"mutual\s+fund|exchange.traded|etf|"
    r"bank\s+of|trust\s+company|savings|credit\s+union|"
    r"insurance|annuity|"
    r"fund\s+of\s+funds|"
    r"securities\s+inc|securities\s+llc|securities\s+group"
    r")\b",
    re.IGNORECASE,
)


# ── Classification logic ─────────────────────────────────────────────────────


def classify_entity(entity: SFOEntity, website_text: str | None = None) -> EntityType:
    """Determine the entity type based on name, website content, and existing metadata.

    Args:
        entity: The SFO entity to classify.
        website_text: Scraped text content from the entity's website (optional).

    Returns:
        The most likely EntityType (SFO, MFO, VC, or UNKNOWN).
    """
    # Start with the entity's current classification
    current = entity.entity_type
    name = entity.entity_name
    combined = name + " " + (website_text or "") + " " + (entity.source_of_wealth or "")

    # ── Check for MFO indicators ──
    if MFO_TRIGGERS.search(combined):
        return EntityType.MFO

    if MFO_NAME_HINTS.search(name) and current == EntityType.SFO:
        # Name sounds like MFO — flag for review
        entity.log(f"CLASSIFIER: Name hint '{name}' suggests possible MFO — flagging for review")
        return EntityType.MFO

    # ── Check for VC indicators ──
    if VC_TRIGGERS.search(combined):
        return EntityType.VC

    # VC name hint + no evidence of single-family wealth → classify as VC
    if VC_NAME_HINTS.search(name):
        # If website text mentions external investors or fund-raising, it's VC
        if website_text and "external" in website_text.lower():
            return EntityType.VC
        # If source_of_wealth suggests VC, also flag
        if entity.source_of_wealth and any(
            kw in entity.source_of_wealth.lower()
            for kw in ["venture", "vc", "fund", "capital investments", "startup"]
        ):
            return EntityType.VC

    # ── Check for non-SFO name patterns ──
    # Only flag if the name doesn't also contain "family" (family + bank = likely SFO)
    if NON_SFO_NAME_PATTERNS.search(name) and "family" not in name.lower():
        entity.log(f"CLASSIFIER: Name '{name}' matches non-SFO pattern — flagging for review")
        return EntityType.UNKNOWN

    # ── Default: SFO ──
    return EntityType.SFO


def validate_sfo_purity(entity: SFOEntity) -> bool:
    """Return True if the entity is confirmed as a pure SFO (not MFO/VC).

    Logs a warning if the entity appears to be misclassified.
    """
    result = classify_entity(entity)
    if result != EntityType.SFO:
        entity.log(
            f"VALIDATION: Entity may be misclassified as SFO — "
            f"classifier suggests {result.value}. Review recommended."
        )
        return False
    return True
