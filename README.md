# Family Office Intelligence Pipeline & Micro-RAG

A data product for discovering, enriching, and querying Single Family Office (SFO) intelligence — sourced from press rankings, directories, conference networks, corporate registries, and SEC Form ADV filings.

## Dataset (v3.0.0)

| Metric | Count | Coverage |
|--------|-------|----------|
| Total entities | 50 | 100% |
| With principals | 48 | 96% |
| With source of wealth | 44 | 88% |
| With AUM | 2 | 4% |
| With website | 15 | 30% |
| With year established | 40 | 80% |

All 50 entities are well-known megabillionaire family offices (Walton, Musk, Gates, Bezos, Buffett, Mars, Bloomberg, Koch, Hearst, Grosvenor, etc.) with manually curated data from secondary sources. Each entity carries **contextual intelligence signals** (sports ownership, philanthropy, portfolio holdings, M&A activity) and **inclusion evidence** — a provenance record documenting how and why the entity was included. The dataset is committed to `data/sfo_enriched.json` with a versioned metadata header.

### Previous version

v2.0.0 contained 59 entities discovered via SEC EDGAR EFTS with deterministic SHA256 IDs. The v3.0.0 migration replaced these entirely with 50 press-ranking/directory-sourced entities using sequential IDs (SFO-001 to SFO-050). See `data/sfo_seed_DEPRECATED_famous_names.json` for the very first prototype.

## What works

- **Pipeline**: Loads seed data, applies enrichment (classifier, website scrape, SEC EDGAR, web search), persists results. Runs in ~30 seconds for 50 entities.
- **RAG engine**: ChromaDB (persistent) with in-memory fallback. TF-IDF scoring with field-aware boosting. Returns ranked results with similarity scores and guardrail notes.
- **Guardrails**: Detects speculative language, flags unresolved contacts, demotes generic inboxes. Tells you what it doesn't know.
- **API**: FastAPI with async pipeline execution (job ID + polling), paginated entity listing, health checks.
- **CLI**: `pipeline`, `query`, `serve`, `export`, `validate` commands.
- **Audit trail**: Structured JSONL logging of every API call, extraction, and failure.
- **Tests**: 67 passing (models, classifier, pipeline, RAG).

## What doesn't work (honest)

- **SEC AUM extraction**: Irrelevant for 48/50 entities — megabillionaire family offices don't file with the SEC as registered investment advisers. AUM data is sourced from press rankings and directories instead.
- **Website scraper**: Finds 0 principals on SFO websites. Family offices don't publish team directories. The scraper architecture is correct but the domain doesn't cooperate.
- **Email discovery**: Hunter.io yields no results — these families operate with extreme privacy. All contacts are tagged "Unresolved" with documented attempt trails.
- **API tests**: 22 FastAPI integration tests hang due to fixture lifecycle issues not yet resolved.
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
python cli.py query "Cascade Investment L.L.C."
python cli.py query "family offices in London"
python cli.py query "sports ownership"

# Start the API server
python cli.py serve

# Run the pipeline (re-enriches all 50 entities)
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
Seed (50 curated SFOs) → Orchestrator → Overrides → Web/SEC Enrichment → Enriched JSON → RAG Engine → API/CLI
```

Data sources:
- **Press rankings**: Altss.com Largest Family Offices list, Forbes billionaires, Bloomberg wealth
- **Directories**: OpenVC.app family office investor lists, TIGER 21 peer networks
- **Conference networks**: Prestel & Partner Family Office Forum Zurich — speaker/attendee rosters
- **Corporate registries**: SEC Form ADV, UK Companies House, Swiss commercial register
- **SEC EDGAR**: Limited utility for megabillionaire FOs — used only for Form ADV-registered entities

Each entity includes structured **signals** (contextual intelligence: sports ownership, legacy wealth, recent investments, impact investing) and **inclusion evidence** (provenance text documenting the source and rationale for inclusion).

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
│   └── sfo.py              # SFOEntity, Principal, ContactMethod, Signal
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
# Fast unit tests (skip API — known hang issue)
pytest tests/ -v -k "not test_api"

# With coverage
pytest tests/ --tb=short -k "not test_api"

# All tests (API tests may hang indefinitely)
pytest tests/ -v
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
