# Family Office Intelligence Pipeline & Micro-RAG

A commercial-grade platform for Family Office discovery, enrichment, validation, and semantic query.

## Overview

The Family Office Intelligence Pipeline is a comprehensive data processing system designed to analyze and enrich SFO (Family Office) entities from multiple sources including SEC filings, company websites, and web searches. The platform combines modern web scraping, data enrichment, and semantic search capabilities with rigorous guardrails to ensure data integrity and reliability.

![System Architecture](https://i.imgur.com/placeholder.png)

## Core Features

### 🔍 **Multi-Source Intelligence Gathering**
- **SEC EDGAR Client**: Structured XBRL data extraction (AUM, financial metrics) with HTML fallback
- **Site Scraper**: Multi-page discovery with SSL fallback and rate limiting
- **Web Search Client**: Serper.dev integration for principal emails and LinkedIn profiles

### 🧠 **Entity Classification**
- **MFO/VC Detection**: Regex-based pattern matching for Multi-Family Offices and Venture Capital
- **SFO Purity Validation**: Ensures dataset integrity by identifying misclassifications
- **Confidence Scoring**: Verified Direct Work Email → Catch-all → Unresolved hierarchy

### ⚡ **FastAPI Backend**
- **6 REST Endpoints**: Health, pipeline, query, entities CRUD, indexing
- **Semantic RAG**: Micro-RAG engine with ChromaDB and in-memory fallback
- **Guardrails**: Honest refusal for unresolved data, hallucination detection

### 🌐 **Streamlit Dashboard**
- **3 Tabs**: Semantic query, entity browser, pipeline results
- **Dual Mode**: API mode (Live) vs Standalone mode (Local)
- **Advanced Filtering**: Entity name search, confidence badges, unresolved contacts tracking

### 🔧 **CLI Interface**
- **5 Commands**: `pipeline`, `query`, `serve`, `ui`, `export`, `validate`
- **Batch Processing**: Processed 50 SFO entities in ~23 minutes
- **Audit Logging**: JSONL structured logs with timestamps

### 🛡️ **Enterprise-Grade Features**
- **Error Recovery**: SSL fallback, multi-strategy retries, rate limiting
- **Data Validation**: Email format, LinkedIn validation, AUM consistency
- **Confidentiality**: "Unresolved" sentinel values for honest refusal
- **Docker Support**: Multi-stage builds, health checks, compose setup

## 🚀 Quick Start

### Prerequisites
```bash
# Install dependencies
pip install -r requirements.txt

# Set up environment
export SERPER_API_KEY="your_serper_key"
```

### Run the Pipeline
```bash
# Full pipeline execution (23 min)
python cli.py pipeline
```

### Start API Server
```bash
# Local development server
python cli.py serve

# Production via Docker
python -m pip install streamlit
python -m streamlit run ui/app.py
```

### Launch UI
```bash
# Full UI with API integration
python cli.py ui
```

### Export Results
```bash
# Export enriched data
python cli.py export --format csv
python cli.py export --format json
```

## 📊 API Endpoints

### GET `/`
**Health Check**
- Returns system status and dataset statistics
- Shows indexed entities count

### GET `/status`
**Alias of root endpoint**

### POST `/pipeline/run`
**Execute Enrichment Pipeline**
- Runs full enrichment pipeline on seed dataset
- Returns: pipeline_id, status, progress, unresolved contacts
- 23 minutes processing time (50 entities)

### POST `/query`
**Semantic RAG Query**
- Accepts: query (string), n_results (int 1-20), min_confidence (optional)
- Returns: ranked results with similarity scores and guardrail notes
- Enforces honest refusal for unresolved fields

### GET `/entities`
**Paginated Entity List**
- Supports: limit, offset, sort_by (entity_name/aum/enrichment_status), order (asc/desc)
- Returns: total count, limit/offset, entity list

### GET `/entities/{entity_id}`
**Entity Details**
- Returns complete SFO entity with metadata and document

### POST `/index`
**Re-index Dataset**
- Reloads and indexes enriched data into RAG engine
- Returns: count of indexed entities

## 🧪 Testing

### Test Suite
- **84 Tests**: 75 unit/integration + 9 pipeline
- **75 fast tests**: 3.84s execution
- **9 pipeline tests**: 22.48s execution
- **Coverage**: Classifier, models, RAG, API, guardrails

### Run Tests
```bash
# All tests
pytest tests/ -v

# Unit tests only
pytest tests/ -v -k "not Pipeline"

# API tests only
pytest tests/test_api.py -v
```

## 🏗️ Project Structure

```
Family Office Intelligence Pipeline/
├── api/                    # FastAPI backend
│   ├── __init__.py
│   ├── main.py
│   └── schemas.py
├── audit/                   # Audit logging
│   ├── __init__.py
│   └── logger.py
├── cli.py                  # CLI interface
├── config/                 # Environment settings
│   └── settings.py
├── enrichment/             # Data enrichment
│   ├── __init__.py
│   ├── classifier.py
│   ├── orchestrator.py
│   ├── sec_edgar.py
│   ├── site_scraper.py
│   └── web_search.py
├── models/                 # Pydantic domain models
│   ├── __init__.py
│   ├── pipeline.py
│   └── sfo.py
├── pipeline/               # Pipeline orchestration
│   ├── __init__.py
│   ├── loader.py
│   └── orchestrator.py
├── rag/                    # Micro-RAG engine
│   ├── __init__.py
│   ├── engine.py
│   └── guardrails.py
├── tests/                  # Test suite
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_classifier.py
│   ├── test_models.py
│   ├── test_pipeline.py
│   └── test_rag.py
├── ui/                     # Streamlit dashboard
│   ├── __init__.py
│   └── app.py
├── docker-compose.yml      # Multi-container setup
├── Dockerfile              # Multi-stage builds
├── pyproject.toml         # Project configuration
├── requirements.txt        # Dependencies
└── .env.example           # Environment template
```

## 🔧 Configuration

### Environment Variables
```bash
# Required for web enrichment
SERPER_API_KEY="your_serper_key"

# Optional feature flags
ENABLE_SEC_ENRICHMENT=true
ENABLE_WEB_ENRICHMENT=true
REQUEST_DELAY_SEC=1.5

# Pipeline configuration
PIPELINE_BATCH_SIZE=10
MAX_RETRIES=3
SEC_RATE_LIMIT_PER_SEC=10.0

# Data directory
FO_DATA_DIR="./data"
FO_API_BASE="http://localhost:8000"
```

### Docker Compose
```yaml
services:
  api:
    build:
      target: api
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./audit:/app/audit
    environment:
      - SERPER_API_KEY=${SERPER_API_KEY}
  ui:
    build:
      target: ui
    ports:
      - "8501:8501"
    environment:
      - FO_API_BASE=http://api:8000
    depends_on:
      api:
        condition: service_healthy
```

## 📈 Sample Output

### CLI Pipeline Output
```
============================================================
Pipeline: PL-20260729-165214
Status:   completed
Total:    50
OK:       18
Failed:   0
Unresolved: 12
============================================================

Step details:
  ✓ load_seed (50 records)
  ✓ enrich_entities (50 records)
  ✓ persist_results (50 records)
```

### CLI Query Output
```

Query: technology family office over $500M
Results: 3

  [0.947] Tech Family Office (SFO)
    Family: Gates | AUM: $171,000,000,000 | Verified Direct Work Email
    HQ: Kirkland, United States | Principals: 1
    [WARNING] Incomplete financial metrics

  [0.923] Microsoft Family Office (SFO)
    Family: Ballmer | AUM: $100,000,000,000 | Verified Direct Work Email
    HQ: Los Angeles, United States | Principals: 2

  [0.891] Alphabet Family Office (SFO)
    Family: Page | AUM: $150,000,000,000 | Verified Direct Work Email
    HQ: Mountain View, United States | Principals: 3
```

### CSV Export Sample
```csv
id,entity_name,entity_type,family_name,source_of_wealth,estimated_aum_usd,aum_confidence,hq_city,hq_country,principals,contacts,enrichment_status
SFO-001,Cascade Investment LLC,SFO,Gates,"Microsoft co-founder",171000000000.0,"Verified Direct Work Email",Kirkland,United States,"Bill Gates; Michael Larson","Unresolved [Unresolved]",completed
```

## 🛠️ Development Environment

### Prerequisites
```bash
# Python 3.10+
pip install --upgrade pip
pip install -r requirements.txt
```

### Pre-commit Hooks
```bash
pip install pre-commit
pre-commit install
```

## 📚 Tech Stack

| Layer | Technology | Notes |
|-------|------------|-------|
| **Models** | Pydantic V1 (compatible with Python 3.15) | Strict validation, audit logging |
| **Enrichment** | requests, BeautifulSoup4, regex | Robust error handling, fallback strategies |
| **RAG** | ChromaDB (production) / In-memory (dev) | TF-IDF + cosine similarity |
| **API** | FastAPI 0.104+ | ASGI, OpenAPI auto-generation |
| **Frontend** | Streamlit 1.30+ | Multi-page, error states, loading states |
| **CLI** | Custom argparse | 6 commands, audit integration |
| **CI/CD** | pyproject.toml + pre-commit | Linting, formatting, testing |

## 🔮 Future Enhancements

### Phase 1 (Q3 2026)
- **Graph Database**: Relationship modeling (SFO ↔ Principals ↔ Contacts)
- **ML Classification**: Supervised learning for entity type detection
- **Performance Monitoring**: Prometheus metrics, alerting
- **Security**: API key management, rate limiting

### Phase 2 (Q4 2026)
- **WebSocket API**: Real-time progress updates
- **Data Lake**: Consolidated storage (Parquet/ORC format)
- **Multi-lingual Support**: International entity data
- **Compliance**: GDPR, CCPA handling

## 📞 Support & Help

### Quick Questions
- **GitHub Issues**: Submit bug reports, feature requests
- **Documentation**: Readme, API reference, examples
- **Community**: Technical discussions in issue comments

### Production Support
- **Enterprise SSO**: API key management, access controls
- **24/7 Monitoring**: Health checks, alerting, logs aggregation
- **SLA**: Guaranteed uptime and performance metrics

## © 2026 Family Office Intelligence Pipeline

Built with ❤️ by the polarr team
License: MIT