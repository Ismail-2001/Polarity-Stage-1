# Family Office Intelligence Pipeline & Micro-RAG

A data product for discovering, enriching, and querying Single Family Office (SFO) intelligence from SEC EDGAR filings.

## What this is

A pipeline that takes 59 SEC-registered family offices and enriches them with AUM estimates, principals, source-of-wealth narratives, websites, and contact data. The enriched dataset is queryable via a RAG engine with honest-refusal guardrails — it tells you what it doesn't know instead of making things up.

## Dataset (v2.0.0)

| Metric | Count | Coverage |
|--------|-------|----------|
| Total entities | 59 | 100% |
| With principals | 56 | 95% |
| With source of wealth | 55 | 93% |
| With AUM | 45 | 76% |
| With website | 40 | 68% |
| With email | 36 | 61% |
| With year established | 40 | 68% |

All 59 entities are real SEC-registered family offices with verified CIK numbers. AUM data comes from 13F holdings estimates and manual curation. The dataset is committed to `data/sfo_enriched.json` with a versioned metadata header.

## What works

- **Pipeline**: Loads seed data, applies manual overrides (AUM, principals, emails, SOW, websites), runs SEC enrichment, persists results. Runs in ~40 seconds for 59 entities.
- **RAG engine**: ChromaDB (persistent) with in-memory fallback. TF-IDF scoring with field-aware boosting. Returns ranked results with similarity scores and guardrail notes.
- **Guardrails**: Detects speculative language, flags unresolved contacts, demotes generic inboxes. Tells you what it doesn't know.
- **API**: FastAPI with async pipeline execution (job ID + polling), paginated entity listing, health checks.
- **CLI**: `pipeline`, `query`, `serve`, `export`, `validate` commands.
- **Audit trail**: Structured JSONL logging of every API call, extraction, and failure.
- **Tests**: 89 passing (models, classifier, RAG, API integration).

## What doesn't work (honest)

- **Website scraper**: Finds 0 principals on SFO websites. Family offices don't publish team directories. The scraper architecture is correct but the domain doesn't cooperate.
- **Serper web search**: API key returns 403 (expired/over quota). Falls back to Hunter.io or "Unresolved".
- **SEC AUM extraction**: XBRL returns 404 for all SFO CIKs. 13F XML extraction works for some entities. Most AUM comes from manual overrides.
- **Streamlit UI**: Built and pinned but not verified on the current Python version. Use Docker for cloud deployment.

## Quick start

```bash
# Clone and install
git clone https://github.com/Ismail-2001/Polarity-Stage-1.git
cd Polarity-Stage-1
pip install -r requirements.txt

# See the dataset in action (30 seconds)
python demo.py

# Or query directly
python cli.py query "Duquesne Family Office"
python cli.py query "family offices in New York"
python cli.py query "AUM over $1 billion"

# Start the API server
python cli.py serve

# Run the pipeline (re-enriches all 59 entities)
python cli.py pipeline

# Export to CSV
python cli.py export --format csv
```

## API endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Health check with dataset statistics |
| `GET` | `/health` | Deep health check (data dir, RAG, API keys) |
| `POST` | `/pipeline/run` | Start pipeline (async — returns job ID) |
| `GET` | `/pipeline/status/{job_id}` | Poll pipeline job status |
| `POST` | `/query` | Semantic RAG query with guardrails |
| `GET` | `/entities` | Paginated entity list |
| `GET` | `/entities/{entity_id}` | Single entity detail |
| `POST` | `/index` | Re-index dataset into RAG |

## Architecture

```
Seed (59 SEC SFOs) → Orchestrator → Manual Overrides → SEC Enrichment → Enriched JSON → RAG Engine → API/CLI
```

- **Discovery**: SEC EDGAR EFTS API (533 CIKs filtered to 59 true SFOs)
- **Enrichment**: Manual overrides (curated data) + automated SEC extraction (13F XML/HTML)
- **Classification**: Regex-based MFO/VC/non-SFO detection
- **Storage**: JSON files with versioned metadata header
- **RAG**: ChromaDB persistent store, TF-IDF scoring, honest-refusal guardrails
- **API**: FastAPI with async pipeline execution, CORS, structured errors

## Project structure

```
├── api/                    # FastAPI backend (async pipeline, health, query, entities)
├── audit/                  # Structured JSONL audit logger
├── cli.py                  # Unified CLI (pipeline, query, serve, export, validate)
├── config/settings.py      # Environment-based configuration
├── data/                   # Dataset (VERSION, seed, enriched, manual overrides)
├── demo.py                 # 30-second demo script
├── enrichment/             # Multi-source enrichment engine
│   ├── classifier.py       # MFO/VC/non-SFO regex classifier
│   ├── orchestrator.py     # Per-entity enrichment pipeline
│   ├── sec_edgar.py        # SEC EDGAR XBRL + 13F AUM extractor
│   ├── site_scraper.py     # Website scraper with link-based discovery
│   └── web_search.py       # Serper.dev + Hunter.io fallback
├── models/                 # Pydantic domain models
│   ├── pipeline.py         # PipelineResult, ExecutionStep
│   └── sfo.py              # SFOEntity, Principal, ContactMethod
├── pipeline/               # Batch orchestration and JSON persistence
├── rag/                    # Micro-RAG engine and guardrails
├── tests/                  # 89 tests (models, classifier, RAG, API)
├── ui/                     # Streamlit dashboard
├── .github/workflows/ci.yml  # GitHub Actions CI
├── Dockerfile              # Multi-stage builds (api, pipeline, ui)
├── docker-compose.yml      # Multi-service compose
└── requirements.txt        # Pinned for Python 3.11
```

## Running tests

```bash
# All tests
pytest tests/ -v

# Fast unit tests only
pytest tests/ -v -k "not Pipeline"

# With coverage
pytest tests/ --tb=short
```

## Configuration

All configuration via `.env` or environment variables. See `.env.example`.

```bash
FO_DATA_DIR=data                    # Data directory
ENABLE_SEC_ENRICHMENT=true          # SEC EDGAR enrichment
ENABLE_WEB_ENRICHMENT=true          # Web scraping + search
REQUEST_DELAY_SEC=1.5               # HTTP request delay
SEC_RATE_LIMIT_PER_SEC=10           # SEC fair-access limit
SERPER_API_KEY=                     # Serper.dev (optional)
HUNTER_API_KEY=                     # Hunter.io (optional)
```

## Deployment

**Docker** (recommended):
```bash
docker-compose up --build
```

**Streamlit Cloud**:
1. Push to GitHub
2. Go to share.streamlit.io → New app
3. Point to `ui/app.py`
4. Python 3.11 via `runtime.txt`

**FastAPI** (Railway/Render/Fly.io):
```bash
python cli.py serve
```

## License

MIT

## Repository

https://github.com/Ismail-2001/Polarity-Stage-1
