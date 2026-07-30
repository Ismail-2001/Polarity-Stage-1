"""Streamlit UI for the Family Office Intelligence Pipeline & Micro-RAG.

Operates in two modes:
  - API mode (default)   : queries the FastAPI backend at localhost:8000
  - Standalone mode       : loads data/sfo_enriched.json directly + in-memory RAG
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# ── Preamble: add project root to sys.path so imports work when run directly ──
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Conditional imports for standalone mode
_HAS_REQUESTS = False
_HAS_RAG = False
try:
    import requests

    _HAS_REQUESTS = True
except ImportError:
    pass

try:
    from models.sfo import SFOCollection
    from rag.engine import MicroRAGEngine

    _HAS_RAG = True
except ImportError:
    pass

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="FO Intelligence Pipeline",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Design System CSS ────────────────────────────────────────────────────────
_CUSTOM_CSS = """
<style>
/* ── Design Tokens ────────────────────────────────────────────────────────── */
:root {
    --color-bg-primary: #0f172a;
    --color-bg-secondary: #1e293b;
    --color-bg-tertiary: #334155;
    --color-border: rgba(255,255,255,0.06);
    --color-border-hover: rgba(255,255,255,0.12);
    --color-text-primary: #f1f5f9;
    --color-text-secondary: #94a3b8;
    --color-text-muted: #64748b;
    --color-accent-green: #10b981;
    --color-accent-amber: #f59e0b;
    --color-accent-red: #ef4444;
    --color-accent-blue: #3b82f6;
    --radius-sm: 6px;
    --radius-md: 8px;
    --radius-lg: 12px;
    --spacing-xs: 4px;
    --spacing-sm: 8px;
    --spacing-md: 16px;
    --spacing-lg: 24px;
    --font-sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* ── Typography ───────────────────────────────────────────────────────────── */
h1, h2, h3, h4, h5, h6, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
    font-family: var(--font-sans) !important;
    color: var(--color-text-primary) !important;
    letter-spacing: -0.01em;
}

/* ── Cards ────────────────────────────────────────────────────────────────── */
.entity-card {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: var(--spacing-md);
    margin-bottom: var(--spacing-sm);
    background: var(--color-bg-secondary);
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.entity-card:hover {
    border-color: var(--color-border-hover);
    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

/* ── Confidence Indicators ────────────────────────────────────────────────── */
.confidence-dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
    flex-shrink: 0;
}
.confidence-dot--verified { background: var(--color-accent-green); }
.confidence-dot--catchall { background: var(--color-accent-amber); }
.confidence-dot--unresolved { background: var(--color-accent-red); }
.confidence-dot--unverified { background: var(--color-text-muted); }

.confidence-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-weight: 600;
    font-size: 0.85em;
    padding: 2px 8px;
    border-radius: var(--radius-sm);
    background: rgba(255,255,255,0.05);
}
.confidence-badge--verified { color: var(--color-accent-green); }
.confidence-badge--catchall { color: var(--color-accent-amber); }
.confidence-badge--unresolved { color: var(--color-accent-red); }
.confidence-badge--unverified { color: var(--color-text-muted); }

/* ── Chips ────────────────────────────────────────────────────────────────── */
button[key^="chip_"] {
    background: var(--color-bg-tertiary) !important;
    border: 1px solid var(--color-border-hover) !important;
    color: var(--color-text-secondary) !important;
    border-radius: 20px !important;
    font-size: 0.85em !important;
    transition: all 0.15s ease !important;
}
button[key^="chip_"]:hover {
    border-color: var(--color-accent-blue) !important;
    color: var(--color-text-primary) !important;
    background: rgba(59,130,246,0.1) !important;
}

/* ── KPI Cards ────────────────────────────────────────────────────────────── */
.kpi-value {
    font-size: 1.8rem;
    font-weight: 700;
    color: var(--color-text-primary);
    line-height: 1.2;
}
.kpi-label {
    font-size: 0.8rem;
    color: var(--color-text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 2px;
}

/* ── Sidebar ──────────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    border-right: 1px solid var(--color-border);
}
[data-testid="stSidebar"] .stMarkdown p {
    color: var(--color-text-secondary);
}

/* ── Focus States (Accessibility) ─────────────────────────────────────────── */
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible {
    outline: 2px solid var(--color-accent-blue);
    outline-offset: 2px;
}
.stButton > button:focus-visible {
    outline: 2px solid var(--color-accent-blue);
    outline-offset: 2px;
}

/* ── Expander Styling ─────────────────────────────────────────────────────── */
.streamlit-expanderHeader {
    font-weight: 600 !important;
    color: var(--color-text-primary) !important;
}

/* ── Divider ──────────────────────────────────────────────────────────────── */
hr {
    border: none;
    border-top: 1px solid var(--color-border);
    margin: var(--spacing-md) 0;
}

/* ── Match Highlight ──────────────────────────────────────────────────────── */
mark {
    background: rgba(59,130,246,0.3);
    color: var(--color-text-primary);
    border-radius: 2px;
    padding: 0 2px;
}

/* ── Print Styles ─────────────────────────────────────────────────────────── */
@media print {
    .stDeployButton, header[data-testid="stHeader"], [data-testid="stToolbar"],
    [data-testid="stSidebar"], .stButton { display: none !important; }
    .stApp { margin: 0; padding: 0; }
    body { background: white; color: black; }
}
</style>
"""
st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)

# ── Constants ────────────────────────────────────────────────────────────────

API_BASE = os.getenv("FO_API_BASE", "http://localhost:8000")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ENRICHED_PATH = DATA_DIR / "sfo_enriched.json"
SEED_PATH = DATA_DIR / "sfo_seed.json"

logger = logging.getLogger("fo_ui")

CONFIDENCE_COLORS: dict[str, str] = {
    "Verified Direct Work Email": "#1a7f37",
    "Verified": "#1a7f37",
    "Catch-all / Generic Inbox": "#bf8700",
    "Unresolved": "#cf222e",
    "Unverified": "#656d76",
}
ENRICHMENT_EMOJI: dict[str, str] = {
    "completed": "✅",
    "partial": "⚠️",
    "failed": "❌",
    "pending": "⚪",
}

# ── Session state ────────────────────────────────────────────────────────────

if "rag_ready" not in st.session_state:
    st.session_state.rag_ready = False
if "standalone_mode" not in st.session_state:
    st.session_state.standalone_mode = False
if "entities" not in st.session_state:
    st.session_state.entities = []
if "pipeline_running" not in st.session_state:
    st.session_state.pipeline_running = False


# ── Helpers ──────────────────────────────────────────────────────────────────


def _confidence_badge(confidence: str) -> str:
    safe = html.escape(confidence)
    css_map = {
        "Verified Direct Work Email": "verified",
        "Verified": "verified",
        "Catch-all / Generic Inbox": "catchall",
        "Unresolved": "unresolved",
    }
    variant = css_map.get(confidence, "unverified")
    return f'<span class="confidence-badge confidence-badge--{variant}">{safe}</span>'


def _confidence_dot(confidence: str) -> str:
    css_map = {
        "Verified Direct Work Email": "verified",
        "Verified": "verified",
        "Catch-all / Generic Inbox": "catchall",
        "Unresolved": "unresolved",
    }
    variant = css_map.get(confidence, "unverified")
    return f'<span class="confidence-dot confidence-dot--{variant}" title="{html.escape(confidence)}"></span>'


def _get_entity_confidence(entity: dict) -> str:
    contacts = entity.get("contacts", [])
    if not contacts:
        return "Unverified"
    confidences = [c.get("confidence", "Unverified") for c in contacts]
    if any(c == "Verified Direct Work Email" or c == "Verified" for c in confidences):
        return "Verified"
    if any(c == "Catch-all / Generic Inbox" for c in confidences):
        return "Catch-all"
    return "Unresolved"


def _call_api(method: str, path: str, **kwargs) -> dict | None:
    if not _HAS_REQUESTS:
        return None
    try:
        url = f"{API_BASE}{path}"
        resp = requests.request(method, url, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        return None
    except Exception:
        logger.exception("API request failed: %s %s", method, path)
        return None


def _load_local_data() -> list[dict]:
    path = ENRICHED_PATH if ENRICHED_PATH.exists() else SEED_PATH
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict) and "entities" in raw:
        return raw["entities"]
    if isinstance(raw, list):
        return raw
    return []


def _build_local_rag(data: list[dict]):
    """Build an in-memory RAG engine from local JSON data."""
    if not _HAS_RAG or not data:
        return None
    try:
        collection = SFOCollection()
        for item in data:
            from models.sfo import SFOEntity

            entity = SFOEntity(**item)
            collection.add(entity)
        engine = MicroRAGEngine(persist_dir=None)  # in-memory
        engine.index_collection(collection)
        return engine
    except Exception:
        logger.exception("Failed to build local RAG engine")
        return None


def _init_from_api():
    """Try connecting to the FastAPI backend."""
    status = _call_api("GET", "/status")
    if status and status.get("dataset_loaded"):
        return "api"
    return None


def _init_standalone():
    """Load data directly from the JSON files + in-memory RAG."""
    data = _load_local_data()
    if not data:
        return None
    st.session_state.entities = data
    engine = _build_local_rag(data)
    if engine:
        st.session_state.rag_ready = True
        st.session_state._local_rag = engine
    return "standalone"


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.markdown(
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
    '<span style="font-size:1.6rem;">🏦</span>'
    '<span style="font-size:1.1rem;font-weight:700;color:var(--color-text-primary);letter-spacing:-0.02em;">'
    "FO Intelligence</span>"
    "</div>"
    '<p style="font-size:0.75rem;color:var(--color-text-muted);margin:0 0 8px 0;">'
    "Family Office Discovery & Intelligence</p>",
    unsafe_allow_html=True,
)
st.sidebar.markdown("---")

# Mode indicator
mode = _init_from_api()
if mode != "api":
    mode = _init_standalone()

if mode == "api":
    st.sidebar.success(f"⚡ Connected to API at {API_BASE}")
    st.session_state.standalone_mode = False
elif mode == "standalone":
    st.sidebar.info("📡 Standalone mode (local data)")
    st.session_state.standalone_mode = True
else:
    st.sidebar.warning("No data available. Run the pipeline first.")

st.sidebar.markdown("---")
st.sidebar.markdown("### Pipeline Controls")

if st.sidebar.button("▶ Run Pipeline", use_container_width=True, type="primary"):
    if mode == "api":
        with st.status("Running pipeline...", expanded=True) as status:
            st.write("⏳ Submitting pipeline job...")
            result = _call_api("POST", "/pipeline/run")
            if result:
                job_id = result.get("job_id")
                st.write(f"📋 Job `{job_id}` created. Polling status...")
                while True:
                    time.sleep(2)
                    poll = _call_api("GET", f"/pipeline/status/{job_id}")
                    if poll:
                        job_status = poll.get("status", "")
                        st.write(f"🔄 Status: {job_status}")
                        if job_status in ("completed", "failed"):
                            break
                    else:
                        break
                if poll and poll.get("status") == "completed":
                    status.update(label="✅ Pipeline completed!", state="complete")
                    st.toast("Pipeline completed successfully!", icon="✅")
                    st.rerun()
                else:
                    status.update(label="❌ Pipeline failed", state="error")
                    st.toast("Pipeline failed. Check logs.", icon="❌")
            else:
                status.update(label="❌ Pipeline failed", state="error")
                st.toast("Failed to start pipeline.", icon="❌")
    else:
        st.sidebar.error("API not available. Run: py run_pipeline.py")

if st.sidebar.button("🔄 Re-index", use_container_width=True):
    if mode == "api":
        with st.spinner("Re-indexing..."):
            result = _call_api("POST", "/index")
        if result:
            count = result.get("indexed_entities", 0)
            st.toast(f"Indexed {count} entities", icon="✅")
            st.rerun()
        else:
            st.toast("Re-index failed.", icon="❌")
    else:
        data = _load_local_data()
        if data:
            engine = _build_local_rag(data)
            if engine:
                st.session_state._local_rag = engine
                st.session_state.rag_ready = True
                st.toast(f"Indexed {len(data)} entities locally", icon="✅")
                st.rerun()

st.sidebar.markdown("---")
st.sidebar.markdown("### Dataset Stats")
data = st.session_state.entities or _load_local_data()
if data:
    total = len(data)
    unresolved = sum(
        1 for e in data for c in e.get("contacts", []) if c.get("confidence") == "Unresolved"
    )
    verified = sum(
        1 for e in data for c in e.get("contacts", [])
        if c.get("confidence") in ("Verified Direct Work Email", "Verified")
    )
    st.sidebar.metric("Entities", total)
    st.sidebar.metric("Unresolved Contacts", unresolved, delta_color="inverse")
    st.sidebar.metric("Verified Contacts", verified)

with st.sidebar.expander("Confidence Legend", expanded=False):
    st.markdown(
        '<span class="confidence-dot confidence-dot--verified"></span> Verified Direct Work Email  ',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<span class="confidence-dot confidence-dot--catchall"></span> Catch-all / Generic Inbox  ',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<span class="confidence-dot confidence-dot--unresolved"></span> Unresolved  ',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<span class="confidence-dot confidence-dot--unverified"></span> Unverified  ',
        unsafe_allow_html=True,
    )

# ── Main ─────────────────────────────────────────────────────────────────────

st.title("Family Office Intelligence Pipeline")
st.markdown(
    "_Commercial-grade SFO discovery, enrichment, validation, and semantic query._"
)

# ── P0: Pipeline health banner ──────────────────────────────────────────────

data_for_banner = st.session_state.entities or _load_local_data()
if data_for_banner:
    total = len(data_for_banner)
    verified_count = sum(
        1 for e in data_for_banner for c in e.get("contacts", [])
        if c.get("confidence") in ("Verified Direct Work Email", "Verified")
    )
    unresolved_count = sum(
        1 for e in data_for_banner for c in e.get("contacts", [])
        if c.get("confidence") == "Unresolved"
    )
    aum_known = sum(1 for e in data_for_banner if e.get("estimated_aum_usd"))

    if verified_count < total:
        st.warning(
            f"📊 **{verified_count}/{total}** contacts verified · "
            f"**{unresolved_count}/{total}** unresolved · "
            f"**{aum_known}/{total}** AUM known · "
            f"Run enrichment to populate verified emails."
        )

tab1, tab2, tab3 = st.tabs(["🔍 Semantic Query", "📋 Entity Browser", "📈 Pipeline Results"])

# =========================== TAB 1: Query ===========================

with tab1:
    st.subheader("Semantic Search")

    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input(
            "Query",
            placeholder="e.g. technology family office over $500M",
            label_visibility="collapsed",
        )
    with col2:
        n_results = st.number_input("Results", min_value=1, max_value=20, value=5)

    # Example query chips
    st.markdown("**Try these:**")
    chip_cols = st.columns(4)
    example_queries = [
        "Cascade Investment Gates",
        "sports ownership family office",
        "family offices in London",
        "cannabis agriculture investment",
    ]
    for i, eq in enumerate(example_queries):
        with chip_cols[i]:
            if st.button(eq, key=f"chip_{i}", use_container_width=True):
                query = eq
                st.rerun()

    if query:
        results = []
        guardrail_notes = []
        if mode == "api":
            resp = _call_api("POST", "/query", json={"query": query, "n_results": n_results})
            if resp:
                results = resp.get("results", [])
                guardrail_notes = resp.get("guardrail_notes", [])
        elif st.session_state.rag_ready and hasattr(st.session_state, "_local_rag"):
            try:
                resp = st.session_state._local_rag.query(query, n_results=n_results)
                results = resp.get("results", [])
                guardrail_notes = resp.get("guardrail_notes", [])
            except Exception as e:
                logger.exception("RAG query failed")
                st.error(f"Query error: {e}")

        for note in guardrail_notes:
            st.warning(note)

        st.markdown(f"**{len(results)}** result(s)")

        for r in results:
            with st.expander(
                f"{r['entity_name']} ({r['entity_type']}) \u2014 score: {r['similarity_score']:.3f}"
            ):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"**Family:** {r['family_name'] or 'N/A'}")
                    st.markdown(f"**Wealth:** {r['source_of_wealth'] or 'N/A'}")
                    aum_conf = r.get("aum_confidence", "")
                    st.markdown(
                        f"**AUM:** ${r['aum']:,.0f} "
                        + _confidence_badge(aum_conf),
                        unsafe_allow_html=True,
                    )
                    st.markdown(f"**HQ:** {r['hq']}")
                with col_b:
                    st.markdown(f"**Principals:** {r['principal_count']}")
                    st.markdown(f"**Verified Contacts:** {r['verified_contact_count']}")
                    est = r.get("enrichment_status", "")
                    _emoji = ENRICHMENT_EMOJI.get(est, "\u26aa")
                    st.markdown(f"**Status:** {_emoji} {est}")
                    if r.get("has_unresolved_contact"):
                        st.error("\u26a0 Has unresolved contact(s)")
                if r.get("unresolved_warning"):
                    st.warning(r["unresolved_warning"])
    else:
        st.info("Enter a query above to search.")

# =========================== TAB 2: Entity Browser ===========================

with tab2:
    st.subheader("Entity Browser")
    entities = st.session_state.entities or _load_local_data()
    if not entities:
        st.info("No entities loaded. Run the pipeline or connect to the API.")
    else:
        col_filter, col_spacer = st.columns([2, 1])
        with col_filter:
            search_filter = st.text_input("Filter by name", placeholder="Type to filter...")

        def _highlight_match(text: str, query: str) -> str:
            if not query:
                return html.escape(text)
            safe = html.escape(text)
            pattern = re.escape(html.escape(query))
            return re.sub(pattern, lambda m: f"<mark>{m.group(0)}</mark>", safe, flags=re.IGNORECASE)

        filtered = (
            [e for e in entities if search_filter.lower() in e.get("entity_name", "").lower()]
            if search_filter
            else entities
        )

        PAGE_SIZE = 10
        total_pages = max(1, -(-len(filtered) // PAGE_SIZE))
        if "entity_page" not in st.session_state:
            st.session_state.entity_page = 1
        if st.session_state.entity_page > total_pages:
            st.session_state.entity_page = total_pages

        col_count, col_page = st.columns([3, 2])
        with col_count:
            st.markdown(f"**{len(filtered)}** entities · page **{st.session_state.entity_page}/{total_pages}**")
        with col_page:
            page_cols = st.columns([1, 1, 1])
            with page_cols[0]:
                if st.button("Prev", disabled=st.session_state.entity_page <= 1, use_container_width=True):
                    st.session_state.entity_page -= 1
                    st.rerun()
            with page_cols[1]:
                st.number_input(
                    "Page", min_value=1, max_value=total_pages,
                    value=st.session_state.entity_page, key="page_input",
                    label_visibility="collapsed",
                    on_change=lambda: st.session_state.update(entity_page=st.session_state.page_input),
                )
            with page_cols[2]:
                if st.button("Next", disabled=st.session_state.entity_page >= total_pages, use_container_width=True):
                    st.session_state.entity_page += 1
                    st.rerun()

        start = (st.session_state.entity_page - 1) * PAGE_SIZE
        page_entities = filtered[start : start + PAGE_SIZE]

        for ent in page_entities:
            name = ent.get("entity_name", "Unknown")
            etype = ent.get("entity_type", "SFO")
            conf = _get_entity_confidence(ent)
            aum = ent.get("estimated_aum_usd")
            aum_str = f"${aum:,.0f}" if aum else "—"
            dot = _confidence_dot(conf)
            family = ent.get("family_name", "") or ""
            hq = ent.get("hq_city", "") or ""
            country = ent.get("hq_country", "") or ""

            highlighted_name = _highlight_match(name, search_filter)
            preview_parts = [f"{dot}**{highlighted_name}**"]
            if family:
                preview_parts.append(f"Family: {html.escape(family)}")
            if hq:
                preview_parts.append(f"HQ: {html.escape(hq)}, {html.escape(country)}")
            preview_parts.append(f"AUM: {aum_str}")
            preview_parts.append(f"Confidence: {html.escape(conf)}")

            with st.expander(" · ".join(preview_parts), expanded=False):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(f"**Entity ID:** `{ent.get('id', 'N/A')}`")
                    st.markdown(f"**Type:** {html.escape(etype)}")
                    st.markdown(f"**Family:** {html.escape(family) or 'N/A'}")
                    st.markdown(f"**Source of Wealth:** {html.escape(ent.get('source_of_wealth') or 'N/A')}")
                    st.markdown(f"**Year Established:** {html.escape(str(ent.get('year_established') or 'N/A'))}")
                    website = ent.get("website")
                    if website:
                        st.markdown(f"**Website:** [{html.escape(website)}]({html.escape(website)})")
                    else:
                        st.markdown("**Website:** —")
                with col_b:
                    principals = ent.get("principals", [])
                    st.markdown(f"**Principals ({len(principals)}):**")
                    for p in principals:
                        st.markdown(f"- **{html.escape(p.get('full_name', 'Unknown'))}** — {html.escape(p.get('title', ''))}")
                    contacts = ent.get("contacts", [])
                    st.markdown(f"**Contacts ({len(contacts)}):**")
                    for c in contacts:
                        cval = html.escape(c.get("value", "Unresolved"))
                        cconf = c.get("confidence", "Unverified")
                        cdot = _confidence_dot(cconf)
                        st.markdown(f"  {cdot} {cval} ({html.escape(cconf)})", unsafe_allow_html=True)

                signals = ent.get("signals", [])
                if signals:
                    st.markdown("**Signals:**")
                    for sig in signals:
                        st.caption(f"• {html.escape(sig.get('type', ''))}: {html.escape(sig.get('description', ''))}")

                inclusion = ent.get("inclusion_evidence")
                if inclusion:
                    st.markdown("**Inclusion Evidence:**")
                    st.caption(html.escape(inclusion))

# =========================== TAB 3: Pipeline Results ==========================

with tab3:
    st.subheader("Pipeline Output")

    entities = st.session_state.entities or _load_local_data()
    if entities:
        total = len(entities)
        unresolved = sum(
            1 for e in entities for c in e.get("contacts", []) if c.get("confidence") == "Unresolved"
        )
        verified = sum(
            1 for e in entities
            for c in e.get("contacts", [])
            if c.get("confidence") in ("Verified Direct Work Email", "Verified")
        )
        catch_all = sum(
            1 for e in entities
            for c in e.get("contacts", [])
            if c.get("confidence") == "Catch-all / Generic Inbox"
        )
        aum_known = sum(1 for e in entities if e.get("estimated_aum_usd"))

        kpi_cols = st.columns(5)
        kpi_cols[0].metric("Total Entities", total)
        kpi_cols[1].metric("Verified Contacts", verified)
        kpi_cols[2].metric("Catch-all", catch_all)
        kpi_cols[3].metric("Unresolved", unresolved)
        kpi_cols[4].metric("AUM Known", aum_known)

        st.markdown("---")

        st.markdown("**Contact Confidence Distribution**")

        total_contacts = verified + catch_all + unresolved
        if total_contacts > 0:
            v_pct = verified / total_contacts * 100
            c_pct = catch_all / total_contacts * 100
            u_pct = unresolved / total_contacts * 100
            st.markdown(
                f'<div style="display:flex;gap:24px;margin:8px 0;">'
                f'<span style="color:var(--color-accent-green);font-weight:600;">'
                f"Verified {verified} ({v_pct:.0f}%)</span>"
                f'<span style="color:var(--color-accent-amber);font-weight:600;">'
                f"Catch-all {catch_all} ({c_pct:.0f}%)</span>"
                f'<span style="color:var(--color-accent-red);font-weight:600;">'
                f"Unresolved {unresolved} ({u_pct:.0f}%)</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        chart_data = pd.DataFrame({
            "Status": ["Verified", "Catch-all", "Unresolved"],
            "Count": [verified, catch_all, unresolved],
        })
        st.bar_chart(chart_data.set_index("Status"))

        st.markdown("---")
        dl_cols = st.columns(2)
        with dl_cols[0]:
            st.download_button(
                "📥 Download as JSON",
                data=json.dumps(entities, indent=2, default=str),
                file_name="sfo_export.json",
                mime="application/json",
                use_container_width=True,
            )
        with dl_cols[1]:
            csv_df = pd.DataFrame([
                {
                    "id": e.get("id", ""),
                    "entity_name": e.get("entity_name", ""),
                    "entity_type": e.get("entity_type", ""),
                    "family_name": e.get("family_name", ""),
                    "hq_city": e.get("hq_city", ""),
                    "hq_country": e.get("hq_country", ""),
                    "estimated_aum_usd": e.get("estimated_aum_usd", ""),
                    "source_of_wealth": e.get("source_of_wealth", ""),
                    "contacts": "; ".join(
                        f"{c.get('value','')} ({c.get('confidence','')})"
                        for c in e.get("contacts", [])
                    ),
                }
                for e in entities
            ])
            st.download_button(
                "📥 Download as CSV",
                data=csv_df.to_csv(index=False),
                file_name="sfo_export.csv",
                mime="text/csv",
                use_container_width=True,
            )
    else:
        st.info("No enriched data found. Run the pipeline first.")
