#!/usr/bin/env python3
"""Narrative Intelligence Report Generator.

Runs structured RAG queries across intelligence dimensions and produces
a self-contained markdown report with guardrail notes and data freshness.

Usage:
    python report.py                      # Generate to data/intelligence_report.md
    python report.py --output report.md   # Custom output path
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_project_root = Path(__file__).resolve().parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from config.settings import settings  # noqa: E402
from pipeline.loader import SeedDataLoader  # noqa: E402
from rag.engine import MicroRAGEngine  # noqa: E402

DIVIDER = "=" * 72

# ── Intelligence sections ──────────────────────────────────────────────
# Each section: (title, query, description)
SECTIONS: list[tuple[str, str, str]] = [
    (
        "Largest Single Family Offices by AUM",
        "largest family offices assets under management billion",
        "Top entities ranked by estimated assets under management, "
        "with confidence ratings for each estimate.",
    ),
    (
        "Wealth Sources Across the Dataset",
        "source of wealth technology inheritance real estate energy",
        "Breakdown of how wealth was generated across all tracked families, "
        "identifying dominant industries and notable concentrations.",
    ),
    (
        "Geographic Distribution",
        "headquarters city location New York California Texas",
        "Where these family offices are based, highlighting concentration "
        "in major financial and tech hubs.",
    ),
    (
        "Notable Principals and Leadership",
        "founder chairman CEO principal leadership managing partner",
        "Key individuals driving these family offices — founders, chairs, "
        "and managing partners identified in public filings.",
    ),
    (
        "Contact Quality and Verification Status",
        "email contact verified unresolved catch-all",
        "Assessment of contact data quality across the dataset — how many "
        "emails are verified vs. catch-all vs. unresolved.",
    ),
    (
        "Year Established — Vintage Analysis",
        "year established founded vintage decade",
        "When these offices were founded, revealing generational patterns "
        "in wealth creation and institutional formation.",
    ),
    (
        "Multi-Family vs Single-Family Overlap",
        "multi-family office external clients MFO SFO",
        "Entities that blur the line between SFO and MFO, offering services "
        "to external families or managing multi-generational wealth.",
    ),
    (
        "Emerging and Undocumented Offices",
        "new established recent emerging unknown",
        "Recently formed offices or those with limited public information, "
        "representing potential research targets.",
    ),
]


def _fmt_aum(aum: float | None) -> str:
    if aum is None:
        return "Unresolved"
    if aum >= 1_000_000_000:
        return f"${aum / 1_000_000_000:.1f}B"
    if aum >= 1_000_000:
        return f"${aum / 1_000_000:.0f}M"
    return f"${aum:,.0f}"


def generate_report(output_path: Path) -> None:
    print(f"\n{DIVIDER}")
    print("  FAMILY OFFICE INTELLIGENCE — NARRATIVE REPORT")
    print(DIVIDER)

    # ── Load data ──────────────────────────────────────────────────────
    print("\n[1/3] Loading dataset...")
    loader = SeedDataLoader(settings.resolved_data_dir)
    enriched_path = loader.data_dir / "sfo_enriched.json"
    seed_path = loader.data_dir / "sfo_seed.json"

    if enriched_path.exists():
        collection = loader.load_json("sfo_enriched.json")
    elif seed_path.exists():
        collection = loader.load_json("sfo_seed.json")
    else:
        print("  ERROR: No data files found. Run 'python cli.py pipeline' first.")
        sys.exit(1)

    entities = collection.entities
    total = len(entities)
    print(f"  {total} entities loaded")

    # ── Compute dataset stats ──────────────────────────────────────────
    print("[2/3] Computing dataset statistics...")
    emails_verified = sum(
        1 for e in entities
        if any(c.confidence.value == "Verified Direct Work Email" for c in e.contacts)
    )
    emails_catchall = sum(
        1 for e in entities
        if any(c.confidence.value == "Catch-all / Generic Inbox" for c in e.contacts)
    )
    emails_unresolved = sum(
        1 for e in entities
        if any(c.confidence.value == "Unresolved" for c in e.contacts)
    )
    principals_count = sum(1 for e in entities if e.principals)
    aum_resolved = sum(1 for e in entities if e.estimated_aum_usd is not None)
    sow_count = sum(1 for e in entities if e.source_of_wealth)
    website_count = sum(1 for e in entities if e.website)
    year_count = sum(1 for e in entities if e.year_established)

    aum_values = [e.estimated_aum_usd for e in entities if e.estimated_aum_usd is not None]
    total_aum = sum(aum_values) if aum_values else 0

    freshness = max(
        (e.last_verified_at for e in entities if e.last_verified_at),
        default=None,
    )

    # ── Build RAG engine ───────────────────────────────────────────────
    print("[3/3] Building RAG index and running queries...")
    rag = MicroRAGEngine()
    rag.index_collection(collection)
    print(f"  Indexed {rag.count()} entities")

    # ── Generate report ────────────────────────────────────────────────
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines.append("# Family Office Intelligence Report")
    lines.append("")
    lines.append(f"**Generated:** {now}")
    lines.append(f"**Dataset:** {total} SEC-registered Single Family Offices")
    lines.append("**Source:** Polarity IQ — SFO Intelligence Pipeline")
    if freshness:
        lines.append(f"**Last Verified:** {freshness.strftime('%Y-%m-%d')}")
    lines.append("")

    # ── Executive Summary ──────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"This report covers **{total}** SEC-registered single family offices "
                 f"with a combined estimated AUM of **{_fmt_aum(total_aum)}**.")
    lines.append("")
    lines.append(f"- **{principals_count}** entities have identified principals ({principals_count/total*100:.0f}%)")
    lines.append(f"- **{emails_verified}** have verified direct email contacts")
    lines.append(f"- **{emails_catchall}** have catch-all/generic inbox contacts")
    lines.append(f"- **{emails_unresolved}** have unresolved contact information")
    lines.append(f"- **{aum_resolved}** have AUM estimates ({aum_resolved/total*100:.0f}%)")
    lines.append(f"- **{sow_count}** have documented source of wealth ({sow_count/total*100:.0f}%)")
    lines.append(f"- **{website_count}** have websites ({website_count/total*100:.0f}%)")
    lines.append(f"- **{year_count}** have year-established data ({year_count/total*100:.0f}%)")
    lines.append("")

    # ── Section queries ────────────────────────────────────────────────
    for i, (title, query, description) in enumerate(SECTIONS, 1):
        print(f"  [{i}/{len(SECTIONS)}] {title}")
        result = rag.query(query, n_results=10)

        lines.append("---")
        lines.append("")
        lines.append(f"## {i}. {title}")
        lines.append("")
        lines.append(description)
        lines.append("")

        if result["result_count"] == 0:
            lines.append("*No results found for this query.*")
            lines.append("")
            continue

        # Results table
        lines.append("| # | Entity | AUM | Confidence | HQ | Principals |")
        lines.append("|---|--------|-----|------------|-----|-----------|")
        for j, r in enumerate(result["results"][:8], 1):
            name = r["entity_name"]
            aum = _fmt_aum(r["aum"]) if r["aum"] else "—"
            conf = r["aum_confidence"] or "Unknown"
            hq = r["hq"] if r["hq"].strip(", ") else "—"
            pc = r["principal_count"] or 0
            lines.append(f"| {j} | {name} | {aum} | {conf} | {hq} | {pc} |")
        lines.append("")

        # Guardrail notes
        if result["guardrail_notes"]:
            lines.append("**Data Quality Notes:**")
            for note in result["guardrail_notes"][:3]:
                lines.append(f"- {note}")
            lines.append("")

    # ── Methodology ────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("- **Data Source:** SEC EDGAR filings (13F, Form D, DEF 14A, S-1)")
    lines.append("- **Enrichment:** Automated scraping, XBRL parsing, and manual verification")
    lines.append("- **Retrieval:** TF-IDF keyword matching with field-aware boosting")
    lines.append("- **Guardrails:** Hallucination detection, unresolved contact warnings, "
                 "confidence filtering")
    lines.append("- **Confidence Levels:**")
    lines.append("  - *Confirmed* — Verified from primary source")
    lines.append("  - *Estimated* — Derived from public filings or third-party data")
    lines.append("  - *Unresolved* — Unable to verify from available sources")
    lines.append("  - *Unknown* — No data available")
    lines.append("")

    # ── Disclaimer ─────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## Disclaimer")
    lines.append("")
    lines.append("This report is generated from public SEC filings and automated enrichment. "
                 "AUM figures are estimates and may not reflect current assets. "
                 "Contact information is best-effort and may be outdated. "
                 "This data is for research purposes only and does not constitute "
                 "financial advice or an endorsement of any family office.")
    lines.append("")

    # ── Write output ───────────────────────────────────────────────────
    report_text = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_text, encoding="utf-8")
    print(f"\n  Report written to {output_path}")
    print(f"  {len(lines)} lines, {len(report_text):,} bytes")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate intelligence report")
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=settings.resolved_data_dir / "intelligence_report.md",
        help="Output file path (default: data/intelligence_report.md)",
    )
    args = parser.parse_args()
    generate_report(args.output)


if __name__ == "__main__":
    main()
