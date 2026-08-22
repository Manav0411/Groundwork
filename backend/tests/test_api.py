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
    assert body["citations"]
    assert body["trace"]
    # `project-atlas` is a synthetic demo project, so the answer is graded `ambiguous` and the
    # use of demo fixture data is disclosed rather than presented as synchronized project data.
    assert body["retrieval_grade"] == "ambiguous"
    assert any("synthetic demo workspace" in gap for gap in body["unresolved_gaps"])


def test_query_on_real_project_never_fabricates_evidence() -> None:
    """A project that is not the synthetic demo must not borrow its fixture evidence."""
    response = client.post(
        "/query",
        headers={"X-API-Key": settings.app_api_key},
        json={"query": "What blockers are delaying delivery?", "project_id": "askbase"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert body["evidence"] == []
    assert body["retrieval_grade"] == "incorrect"
    assert body["unresolved_gaps"]
    # The Project Atlas demo fiction must not appear in an unrelated project's answer.
    assert "Stripe Connect" not in body["answer"]
    assert "Sprint 24" not in body["answer"]


def test_trace_durations_are_measured_not_hardcoded() -> None:
    """Guards the fabricated `42/118/430/86/55` literals the trace used to report."""
    fabricated = {42, 118, 430, 86, 55}
    response = client.post(
        "/query",
        headers={"X-API-Key": settings.app_api_key},
        json={"query": "Generate weekly brief for Project Atlas"},
    )
    durations = [step["duration_ms"] for step in response.json()["trace"]]
    assert durations
    assert not fabricated.issubset(set(durations))


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
