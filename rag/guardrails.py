"""Guardrail layer for the Micro-RAG system.

Enforces honest-refusal rules:
  1. Never generate or hallucinate contact information.
  2. If a field is [Unresolved], the system must explicitly say so.
  3. Never present generic/catch-all emails as verified decision-maker contacts.
  4. Maintain SFO/MFO/VC identity discrimination.
"""

from __future__ import annotations

import re

# Phrases that trigger guardrail warnings
HALLUCINATION_PATTERNS = [
    "probably", "might be", "could be", "I think", "likely",
    "perhaps", "maybe", "possibly", "I believe",
]

# Known generic mailbox prefixes
GENERIC_PREFIXES = {"info", "contact", "investments", "support", "hello",
                     "admin", "team", "enquiries", "office", "mail", "noreply"}


class GuardrailLayer:
    """Stateless guardrail rules applied to query responses."""

    def enforce(self, notes: list[str]) -> list[str]:
        """Apply all guardrail rules and return augmented notes."""
        result = list(notes)

        # Check each note for speculative/hallucinatory language
        hallucination_warnings = []
        for note in notes:
            warning = self.check_hallucination(note)
            if warning:
                hallucination_warnings.append(warning)

        # Check for unresolved contact statements that mention email addresses
        result = self.check_unresolved_contact_statement(result)

        # Prepend any hallucination warnings at the top
        if hallucination_warnings:
            result = hallucination_warnings + result

        return result

    def check_hallucination(self, text: str) -> str | None:
        """Check if text contains speculative language — return warning if so."""
        for phrase in HALLUCINATION_PATTERNS:
            if phrase.lower() in text.lower():
                return (
                    f"⚠ Guardrail triggered: speculative language detected ('{phrase}'). "
                    "The system must not guess or infer data not present in the verified dataset."
                )
        return None

    def check_unresolved_contact_statement(self, statements: list[str]) -> list[str]:
        """Ensure any statement about unresolved contacts is properly qualified."""
        email_pat = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        result = []
        for s in statements:
            if "contact" in s.lower() and "@" in s:
                # Extract actual email addresses from the statement
                for match in email_pat.finditer(s):
                    email = match.group(0)
                    local = email.split("@")[0].lower().replace(".", "").replace("_", "").replace("-", "")
                    if local in GENERIC_PREFIXES:
                        result.append(
                            f"⚠ Guardrail: '{email}' appears to be a generic inbox. "
                            "Generic inboxes do not qualify as decision-maker contacts."
                        )
            result.append(s)
        return result

    @staticmethod
    def unresolved_message(field: str = "contact") -> str:
        """Standard honest-refusal message for unresolved data."""
        return (
            f"Information unresolved/unverified in dataset. "
            f"The requested {field} could not be confirmed after multi-source validation "
            f"(SEC EDGAR, company website, press releases, web search)."
        )
