"""Streamlit UI for the Family Office Intelligence Pipeline & Micro-RAG.

Operates in two modes:
  - API mode (default)   : queries the FastAPI backend at localhost:8000
  - Standalone mode       : loads data/sfo_enriched.json directly + in-memory RAG
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
    from rag.engine import MicroRAGEngine
    from pipeline.loader import SeedDataLoader
    from models.sfo import SFOCollection, ContactConfidence

    _HAS_RAG = True
except ImportError:
    pass

# ── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="FO Intelligence Pipeline",
    page_icon="\U0001f3e6",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Constants ────────────────────────────────────────────────────────────────

API_BASE = os.getenv("FO_API_BASE", "http://localhost:8000")
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ENRICHED_PATH = DATA_DIR / "sfo_enriched.json"
SEED_PATH = DATA_DIR / "sfo_seed.json"

CONFIDENCE_COLORS: dict[str, str] = {
    "Verified Direct Work Email": "#1a7f37",
    "Catch-all / Generic Inbox": "#bf8700",
    "Unresolved": "#cf222e",
    "Unverified": "#656d76",
}
ENRICHMENT_EMOJI: dict[str, str] = {
    "completed": "\u2705",
    "partial": "\u26a0\ufe0f",
    "failed": "\u274c",
    "pending": "\u26aa",
}

# ── Session state ────────────────────────────────────────────────────────────

if "rag_ready" not in st.session_state:
    st.session_state.rag_ready = False
if "standalone_mode" not in st.session_state:
    st.session_state.standalone_mode = False
if "entities" not in st.session_state:
    st.session_state.entities = []


# ── Helpers ──────────────────────────────────────────────────────────────────


def _confidence_badge(confidence: str) -> str:
    color = CONFIDENCE_COLORS.get(confidence, "#656d76")
    return f'<span style="color:{color};font-weight:bold;">{confidence}</span>'


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

st.sidebar.title("\U0001f3e6 FO Intelligence")
st.sidebar.markdown("---")

# Mode indicator
mode = _init_from_api()
if mode != "api":
    mode = _init_standalone()

if mode == "api":
    st.sidebar.success(f"\u26a1 Connected to API at {API_BASE}")
    st.session_state.standalone_mode = False
elif mode == "standalone":
    st.sidebar.info("\U0001f4e1 Standalone mode (local data)")
    st.session_state.standalone_mode = True
else:
    st.sidebar.warning("No data available. Run the pipeline first.")

st.sidebar.markdown("---")
st.sidebar.markdown("### Pipeline Controls")

if st.sidebar.button("\u25b6 Run Pipeline", use_container_width=True, type="primary"):
    if mode == "api":
        with st.spinner("Running pipeline..."):
            result = _call_api("POST", "/pipeline/run")
        if result:
            st.sidebar.success(f"Pipeline {result['status']}")
            st.sidebar.json(result, expanded=False)
            st.rerun()
    else:
        st.sidebar.error("API not available. Run: py run_pipeline.py")

if st.sidebar.button("\U0001f504 Re-index", use_container_width=True):
    if mode == "api":
        result = _call_api("POST", "/index")
        if result:
            st.sidebar.success(f"Indexed {result['indexed_entities']} entities")
            st.rerun()
    else:
        data = _load_local_data()
        if data:
            engine = _build_local_rag(data)
            if engine:
                st.session_state._local_rag = engine
                st.session_state.rag_ready = True
                st.sidebar.success(f"Indexed {len(data)} entities locally")
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
        1 for e in data for c in e.get("contacts", []) if c.get("confidence") == "Verified Direct Work Email"
    )
    st.sidebar.metric("Entities", total)
    st.sidebar.metric("Unresolved Contacts", unresolved, delta_color="inverse")
    st.sidebar.metric("Verified Contacts", verified)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Confidence Legend**\n\n"
    "\U0001f7e2 Verified Direct Work Email\n\n"
    "\U0001f7e1 Catch-all / Generic Inbox\n\n"
    "\U0001f534 Unresolved\n\n"
    "\u26aa Unverified"
)

# ── Main ─────────────────────────────────────────────────────────────────────

st.title("Family Office Intelligence Pipeline")
st.markdown(
    "_Commercial-grade SFO discovery, enrichment, validation, and semantic query._"
)

tab1, tab2, tab3 = st.tabs(["\U0001f50d Semantic Query", "\U0001f4cb Entity Browser", "\U0001f4c8 Pipeline Results"])

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
                    st.markdown(
                        f"**Status:** {ENRICHMENT_EMOJI.get(est, '\u26aa')} {est}"
                    )
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
        search_filter = st.text_input("Filter by name", placeholder="Type to filter...")
        filtered = (
            [e for e in entities if search_filter.lower() in e.get("entity_name", "").lower()]
            if search_filter
            else entities
        )
        st.markdown(f"**{len(filtered)}** entities")
        for ent in filtered:
            name = ent.get("entity_name", "Unknown")
            etype = ent.get("entity_type", "")
            with st.expander(f"{name} ({etype})"):
                st.json(ent, expanded=False)

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
            if c.get("confidence") == "Verified Direct Work Email"
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
        st.download_button(
            "\U0001f4e5 Download as JSON",
            data=json.dumps(entities, indent=2, default=str),
            file_name="sfo_export.json",
            mime="application/json",
        )
    else:
        st.info("No enriched data found. Run the pipeline first.")
