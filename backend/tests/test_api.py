from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_query_requires_api_key() -> None:
    response = client.post("/query", json={"query": "Generate weekly brief for Project Atlas"})
    assert response.status_code == 401


def test_query_returns_cited_answer() -> None:
    response = client.post(
        "/query",
        headers={"X-API-Key": settings.app_api_key},
        json={"query": "Generate weekly brief for Project Atlas"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["retrieval_grade"] == "correct"
    assert body["citations"]
    assert body["trace"]


def test_query_uses_fallback_when_ollama_is_unavailable() -> None:
    response = client.post(
        "/query",
        headers={"X-API-Key": settings.app_api_key},
        json={"query": "Generate weekly brief for Project Atlas"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "Project Atlas" in body["answer"]
    assert any(step["name"] == "Ollama Answer Generator" for step in body["trace"])
