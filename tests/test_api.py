"""Integration tests for the FastAPI backend via Starlette TestClient."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

_project_root = Path(__file__).resolve().parent.parent
os.chdir(str(_project_root))

_FAST_ENV = {
    "REQUEST_DELAY_SEC": "0.0",
    "ENABLE_SEC_ENRICHMENT": "false",
    "ENABLE_WEB_ENRICHMENT": "false",
}


def _make_client() -> TestClient:
    """Set fast-env vars, reload api module, return TestClient."""
    os.environ.update(_FAST_ENV)
    import importlib
    import api.main as api_mod
    importlib.reload(api_mod)
    from api.main import app
    return TestClient(app)


def index_seed(client: TestClient):
    return client.post("/index", timeout=30).json()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests - each class gets independent client  (fresh app state)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRootEndpoint:
    @pytest.fixture
    def client(self):
        return _make_client()

    def test_root_returns_status(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "operational"
        assert "indexed_entities" in data
        assert data["version"] == "1.0.0"

    def test_status_same_as_root(self, client):
        root = client.get("/")
        status = client.get("/status")
        assert root.json()["status"] == status.json()["status"]


class TestIndexEndpoint:
    @pytest.fixture
    def client(self):
        return _make_client()

    def test_index_seeds_data(self, client):
        data = index_seed(client)
        assert data["indexed_entities"] > 0
        assert data["status"] == "ok"

    def test_index_is_idempotent(self, client):
        r1 = index_seed(client)
        r2 = index_seed(client)
        assert r1["indexed_entities"] == r2["indexed_entities"]


class TestQueryEndpoint:
    @pytest.fixture
    def client(self):
        c = _make_client()
        index_seed(c)
        return c

    def test_query_returns_results(self, client):
        resp = client.post("/query", json={"query": "technology", "n_results": 3})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "technology"
        assert isinstance(data["results"], list)
        assert isinstance(data["guardrail_notes"], list)

    def test_query_rejects_large_n(self, client):
        resp = client.post("/query", json={"query": "test", "n_results": 100})
        assert resp.status_code == 422

    def test_query_with_confidence_filter(self, client):
        resp = client.post("/query", json={
            "query": "family", "n_results": 5, "min_confidence": "verified_direct",
        })
        assert resp.status_code == 200


class TestEntitiesEndpoint:
    @pytest.fixture
    def client(self):
        c = _make_client()
        index_seed(c)
        return c

    def test_list_entities_paginated(self, client):
        resp = client.get("/entities?limit=10&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        assert data["limit"] == 10
        assert data["offset"] == 0
        assert len(data["results"]) <= 10
        assert "count" in data

    def test_list_entities_defaults(self, client):
        resp = client.get("/entities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 50
        assert data["offset"] == 0

    def test_get_entity_by_id(self, client):
        list_resp = client.get("/entities?limit=1")
        eid = list_resp.json()["results"][0]["id"]
        resp = client.get(f"/entities/{eid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == eid

    def test_get_entity_not_found(self, client):
        resp = client.get("/entities/SFO-NONEXISTENT")
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data

    def test_list_entities_sorted_asc(self, client):
        resp = client.get("/entities?sort_by=entity_name&order=asc&limit=100")
        assert resp.status_code == 200
        data = resp.json()
        lower_names = [r["entity_name"].lower() for r in data["results"]]
        assert lower_names == sorted(lower_names)

    def test_list_entities_sorted_desc(self, client):
        resp = client.get("/entities?sort_by=entity_name&order=desc&limit=100")
        assert resp.status_code == 200
        data = resp.json()
        lower_names = [r["entity_name"].lower() for r in data["results"]]
        assert lower_names == sorted(lower_names, reverse=True)

    def test_list_entity_fields(self, client):
        resp = client.get("/entities?limit=1")
        item = resp.json()["results"][0]
        expected = {"id", "entity_name", "entity_type", "enrichment_status",
                     "aum_confidence", "principal_count", "verified_contacts",
                     "has_unresolved"}
        assert expected.issubset(item.keys()), f"Missing fields: {expected - set(item.keys())}"


class TestPipelineEndpoint:
    @pytest.fixture
    def client(self):
        return _make_client()

    def test_pipeline_run_completes(self, client):
        resp = client.post("/pipeline/run", timeout=120)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_records"] == 50
        assert data["status"] in ("completed", "partial")
        assert data["indexed_entities"] == 50
        assert len(data["steps"]) == 3

    def test_pipeline_all_steps_recorded(self, client):
        resp = client.post("/pipeline/run", timeout=120)
        data = resp.json()
        step_names = [s["step_name"] for s in data["steps"]]
        assert "load_seed" in step_names
        assert "enrich_entities" in step_names
        assert "persist_results" in step_names


class TestErrorHandling:
    @pytest.fixture
    def client(self):
        return _make_client()

    def test_404_entity(self, client):
        resp = client.get("/entities/DOES_NOT_EXIST")
        assert resp.status_code == 404

    def test_invalid_query_params(self, client):
        resp = client.get("/entities?limit=-1")
        assert resp.status_code == 422

    def test_malformed_json_body_is_422(self, client):
        resp = client.post("/query", content="not json",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 422


class TestCORS:
    @pytest.fixture
    def client(self):
        return _make_client()

    def test_cors_headers_present(self, client):
        resp = client.options(
            "/", headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
