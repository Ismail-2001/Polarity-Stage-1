# ── Stage 1: Base ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ── Stage 2: API server ──────────────────────────────────────────────────────
FROM base AS api
COPY . .
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Stage 3: Pipeline runner ─────────────────────────────────────────────────
FROM base AS pipeline
COPY . .
CMD ["python", "run_pipeline.py"]

# ── Stage 4: Streamlit UI ────────────────────────────────────────────────────
FROM base AS ui
RUN pip install streamlit
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
