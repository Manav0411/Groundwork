from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_query_requires_api_key() -> None:
    response = client.post(
        "/query",
        json={"query": "Generate weekly brief for Project Atlas", "project_id": "project-atlas"},
    )
    assert response.status_code == 401


def test_query_requires_an_explicit_project_id() -> None:
    """`project_id` used to default to the demo project.

    A caller that forgot it got a confident, well-cited answer about a project it had not asked
    about — the worst possible failure mode for a multi-project system. It is required now.
    """
    response = client.post(
        "/query",
        headers={"X-API-Key": settings.app_api_key},
        json={"query": "What was the last commit?"},
    )
    assert response.status_code == 422


def test_the_demo_projects_answer_nothing() -> None:
    """`project-atlas` used to return a cited answer here, and the citations were invented.

    The sample projects carried fixture evidence that was ingested as real documents and graded
    `ambiguous` with a disclosure. That made the demo look capable while the corpus was empty. They
    are now genuinely empty projects and take the same no-evidence path as any unsynced project.

    Cited answers are asserted in the integration tier, against real rows.
    """
    response = client.post(
        "/query",
        headers={"X-API-Key": settings.app_api_key},
        json={"query": "Generate weekly brief for Project Atlas", "project_id": "project-atlas"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert body["evidence"] == []
    assert body["retrieval_grade"] == "incorrect"
    assert body["unresolved_gaps"]
    assert body["trace"]
    # The specific fictions that used to be returned from here.
    assert "Stripe Connect" not in body["answer"]
    assert "Sprint 24" not in body["answer"]


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
        json={"query": "Generate weekly brief for Project Atlas", "project_id": "project-atlas"},
    )
    durations = [step["duration_ms"] for step in response.json()["trace"]]
    assert durations
    assert not fabricated.issubset(set(durations))


def test_no_evidence_means_no_generator_call() -> None:
    """One of the two integrity invariants: no evidence, no synthesized answer.

    With nothing retrieved, synthesis returns the disclosure and never reaches the model — so the
    generator step must be absent from the trace entirely, not present and failed.
    """
    response = client.post(
        "/query",
        headers={"X-API-Key": settings.app_api_key},
        json={"query": "Generate weekly brief for Project Atlas", "project_id": "project-atlas"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "could not find any indexed evidence" in body["answer"]
    assert not any(step["name"] == "Ollama Answer Generator" for step in body["trace"])
