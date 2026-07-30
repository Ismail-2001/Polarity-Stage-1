# Family Office Intelligence Pipeline & Micro-RAG

A commercial-grade platform for Family Office discovery, enrichment, validation, and semantic query against SEC filings, company websites, and web search results.

## Overview

The pipeline processes 52 SFO (Single Family Office) entities through a multi-stage enrichment workflow: SEC EDGAR CIK lookup and AUM extraction, company website scraping with SSL fallback, Serper.dev web search for principal contacts, and MFO/VC classification. Enriched data is persisted to JSON and indexed into a ChromaDB-backed Micro-RAG engine, exposed through a FastAPI REST API with a Streamlit dashboard and a unified CLI interface. Discovery sources (SEC EFTS, Wikipedia, web directories) are kept independent to prevent any single source from dominating >50%.

![Architecture](docs/architecture.png)

## Features

| Module | Capability |
|--------|-----------|
| **SEC EDGAR** | Structured XBRL extraction plus Form ADV HTML fallback; rate-limited at 10 req/s |
| **Site Scraper** | Multi-page discovery (`/team`, `/about`, `/leadership`), SSL auto-fallback, exponential backoff retry |
| **Web Search** | Serper.dev wrapper: principal email discovery and LinkedIn profile search; graceful degradation when no API key configured |
| **Classifier** | Regex-based MFO/VC detection; flags non-SFO entities during enrichment |
| **Micro-RAG** | ChromaDB persistent store with in-memory fallback; TF-IDF scoring with field-aware boosting; honest-refusal guardrails |
| **FastAPI** | 6 endpoints: health, pipeline, query, entities CRUD, indexing |
| **Streamlit** | Interactive dashboard with semantic query, entity browser, and pipeline monitor |
| **CLI** | 6 commands: `pipeline`, `query`, `serve`, `ui`, `export`, `validate` |
| **Audit** | Structured JSONL logging of every API call, extraction, and failure |

## Quick Start

```bash
# Clone the repository
git clone https://github.com/Ismail-2001/Polarity-Stage-1.git
cd Polarity-Stage-1

# Install dependencies
pip install -r requirements.txt

# Copy environment template and add your API keys
cp .env.example .env
# Edit .env — set SERPER_API_KEY to enable web enrichment

# Run the full enrichment pipeline (50 entities, ~23 min with SEC+web enrichment)
python cli.py pipeline

# Start the API server
python cli.py serve            # defaults to 0.0.0.0:8000

# Or query directly from the CLI
python cli.py query "technology family office"

# Launch the Streamlit dashboard (requires streamlit installed)
python cli.py ui

# Export enriched data
python cli.py export --format csv
python cli.py export --format json
```

## API Reference

All endpoints return JSON. The API runs on `http://localhost:8000` by default.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Health check with dataset statistics |
| `GET` | `/status` | Alias for root endpoint |
| `POST` | `/pipeline/run` | Execute enrichment pipeline on seed dataset |
| `POST` | `/query` | Semantic RAG query with guardrails |
| `GET` | `/entities` | Paginated, sortable entity list |
| `GET` | `/entities/{entity_id}` | Single entity detail |
| `POST` | `/index` | Re-index dataset into RAG engine |

The `/query` endpoint accepts a JSON body with `query` (string), `n_results` (int, 1–20), and optional `min_confidence` (`verified_direct`).

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Domain models | Pydantic v1 API (pydantic 1.10.x) |
| Enrichment | `requests` + `BeautifulSoup4` + regex |
| RAG engine | ChromaDB (persistent) / in-memory fallback; TF-IDF scoring |
| API framework | FastAPI 0.104.x with auto-generated OpenAPI docs |
| Dashboard | Streamlit 1.36.x |
| CLI | `argparse` with structured audit integration |
| Configuration | `python-dotenv` with `.env` auto-loading |
| Containerization | Docker multi-stage builds with `docker-compose` |
| Testing | `pytest` — 84 tests (75 fast + 9 pipeline) |
| CI config | `pyproject.toml` with Ruff linting rules |

## Project Structure

```
Polarity-Stage-1/
├── api/                    # FastAPI backend — 6 endpoints
├── audit/                  # Structured JSONL audit logger
├── cli.py                  # Unified CLI (pipeline, query, serve, ui, export, validate)
├── config/
│   └── settings.py         # Application configuration from env vars
├── enrichment/             # Multi-source enrichment engine
│   ├── classifier.py       # MFO/VC regex classifier
│   ├── orchestrator.py     # Per-entity enrichment pipeline
│   ├── sec_edgar.py        # SEC EDGAR XBRL + ADV AUM extractor
│   ├── site_scraper.py     # Website scraper with SSL fallback
│   └── web_search.py       # Serper.dev email + LinkedIn search
├── models/                 # Pydantic domain models
│   ├── pipeline.py         # PipelineResult, ExecutionStep, PipelineStatus
│   └── sfo.py              # SFOEntity, Principal, ContactMethod, GuardrailLayer
├── pipeline/               # Batch orchestration and JSON persistence
├── rag/                    # Micro-RAG engine and hallucination guardrails
├── ui/                     # Streamlit dashboard (3 tabs)
├── tests/                  # Full test suite (classifier, models, RAG, API)
├── data/                   # Seed data (committed) + generated outputs (gitignored)
├── docker-compose.yml      # Multi-service compose (api, ui, pipeline)
├── Dockerfile              # Multi-stage builds for api, pipeline, ui
├── requirements.txt        # Pinned versions for Streamlit Cloud
├── .env.example            # Environment variable template
├── pyproject.toml          # Project config + Ruff linting
└── README.md               # You are here
```

## Configuration

All configuration lives in `.env` (root) or environment variables. See `.env.example` for the full template. Key variables:

```bash
# API keys (required for enrichment depth)
SERPER_API_KEY=           # Serper.dev — free tier: 2500 queries/month
OPENAI_API_KEY=           # Optional — for LLM-enhanced features

# Feature flags
ENABLE_SEC_ENRICHMENT=true
ENABLE_WEB_ENRICHMENT=true

# Pipeline tuning
REQUEST_DELAY_SEC=1.5     # Delay between HTTP requests
MAX_RETRIES=3             # Retries per failed HTTP call
SEC_RATE_LIMIT_PER_SEC=10 # SEC EDGAR fair-access limit
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run fast unit tests only (exclude pipeline)
pytest tests/ -v -k "not Pipeline"

# Run a single test file
pytest tests/test_rag.py -v
```

Test coverage spans the classifier (pattern matching, SFO purity validation), domain models (validation, confidence downgrading, audit logging), Micro-RAG (indexing, querying, guardrails, deduplication), and API endpoints (CRUD, error handling, CORS, pagination, sorting).

## Deployment

### Local Development
```bash
python cli.py serve
```

### Docker Compose
```bash
docker-compose up --build
```

### Streamlit Cloud
1. Push to GitHub (already connected)
2. Go to [share.streamlit.io](https://share.streamlit.io) → New app
3. Point to `ui/app.py`
4. Python 3.11 pinned via `runtime.txt` (auto-detected)
5. Use `requirements.txt` for packages

### FastAPI (lightweight, no RAG)
```bash
# Deploy to Railway / Render / Fly.io as a standalone API
python cli.py serve
```

### Requirements
- Python 3.11+ (3.15 beta works locally but Streamlit has no pre-built numpy wheels yet — use `runtime.txt` for cloud deploys)
- `pip install -r requirements.txt`
- Set `SERPER_API_KEY` in `.env` for full enrichment

## License

MIT

## Repository

https://github.com/Ismail-2001/Polarity-Stage-1
