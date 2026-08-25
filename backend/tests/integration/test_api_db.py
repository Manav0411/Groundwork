"""The HTTP surface against a real, seeded database.

`tests/test_api.py` passes *because* there is no database: `get_optional_session` yields `None`
when Postgres is unreachable, so those tests only ever exercise the synthetic-workspace fallback
and never touch the retrieval path the product actually runs. Overriding the dependency closes
that hole.

Ollama is unreachable here (the root `conftest.py` sees to that), so answers come from the
deterministic fallback. What is being asserted is the evidence and citation contract around the
answer, not its prose — the eval harnesses own generation quality.
"""

import httpx
import pytest
import pytest_asyncio

from app.core.config import settings
from app.db.session import get_optional_session, get_session
from app.main import app
from app.services.ingestion import IngestDocument, ingest_documents

pytestmark = pytest.mark.integration

HEADERS = {"X-API-Key": settings.app_api_key}


@pytest_asyncio.fixture
async def client(session):
    """An ASGI client that shares this test's event loop and database session.

    Not `TestClient`: it drives the app from its own event loop on a worker thread, and the asyncpg
    connection behind `session` belongs to the test's loop. Crossing the two raises "attached to a
    different loop" from deep inside the driver. `httpx.AsyncClient` over `ASGITransport` keeps
    everything on one loop.

    Both session dependencies are overridden. `app/db/session.py` builds its engine at import time
    from `settings.database_url`, so any route left un-overridden quietly connects to whatever
    Postgres that URL names — on a developer machine usually a personal one, and the resulting
    "role does not exist" is a confusing way to learn the test never used the test database.
    """

    async def _override():
        yield session

    app.dependency_overrides[get_optional_session] = _override
    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()


async def _seed_commit(session, *, external_id: str, content: str, title: str) -> None:
    await ingest_documents(
        session,
        [
            IngestDocument(
                project_id="test-project",
                source_type="github",
                external_id=external_id,
                title=title,
                content=content,
                url=f"https://github.com/acme/test/commit/{external_id}",
                author="Raghav Rao",
                author_identities=["raghav rao"],
            )
        ],
        None,
    )


async def test_query_answers_from_the_indexed_corpus(session, project, client) -> None:
    await _seed_commit(
        session,
        external_id="sha-1",
        title="Harden the deployment rollback",
        content="Reworked the deployment rollback so a failed migration reverts cleanly.",
    )

    response = await client.post(
        "/query",
        headers=HEADERS,
        json={"query": "What deployment rollback work happened?", "project_id": "test-project"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["citations"], "A question the corpus answers must produce citations."
    assert body["citations"][0]["url"] == "https://github.com/acme/test/commit/sha-1"
    assert body["citations"][0]["source_type"] == "github"
    assert body["evidence"][0]["citation_id"] == body["citations"][0]["id"]


async def test_a_question_with_no_matching_evidence_is_refused(session, project, client) -> None:
    """The integrity invariant: no evidence means no synthesized answer, with the gap stated."""
    await _seed_commit(
        session,
        external_id="sha-1",
        title="Harden the deployment rollback",
        content="Reworked the deployment rollback so a failed migration reverts cleanly.",
    )

    response = await client.post(
        "/query",
        headers=HEADERS,
        json={"query": "What is the Sprint 24 delivery velocity?", "project_id": "test-project"},
    )

    body = response.json()
    assert body["retrieval_grade"] == "incorrect"
    assert body["citations"] == []
    assert body["evidence"] == []
    assert body["unresolved_gaps"]


async def test_a_real_project_never_borrows_synthetic_evidence(session, project, client) -> None:
    """The fabrication bug, now checked with a database present rather than absent.

    The earlier version of this assertion ran with no database at all, so it could not tell a
    correctly scoped query from one that never reached Postgres.
    """
    response = await client.post(
        "/query",
        headers=HEADERS,
        json={"query": "What blockers are delaying delivery?", "project_id": "test-project"},
    )

    body = response.json()
    assert body["citations"] == []
    assert "Stripe Connect" not in body["answer"]
    assert "Sprint 24" not in body["answer"]


async def test_every_citation_marker_in_the_answer_resolves(session, project, client) -> None:
    """The second integrity invariant: an `[n]` with no matching citation must never ship."""
    import re

    await _seed_commit(
        session,
        external_id="sha-1",
        title="Harden the deployment rollback",
        content="Reworked the deployment rollback so a failed migration reverts cleanly.",
    )

    body = (
        await client.post(
            "/query",
            headers=HEADERS,
            json={"query": "What deployment rollback work happened?", "project_id": "test-project"},
        )
    ).json()

    markers = {int(value) for value in re.findall(r"\[(\d+)\]", body["answer"])}
    assert markers <= {citation["id"] for citation in body["citations"]}


async def test_answers_are_scoped_to_the_requested_project(
    session, project, other_project, client
) -> None:
    await ingest_documents(
        session,
        [
            IngestDocument(
                project_id="other-project",
                source_type="github",
                external_id="sha-x",
                title="Secret work in another project",
                content="Reworked the deployment rollback in a project you did not ask about.",
                url="https://github.com/acme/other/commit/sha-x",
            )
        ],
        None,
    )

    body = (
        await client.post(
            "/query",
            headers=HEADERS,
            json={"query": "What deployment rollback work happened?", "project_id": "test-project"},
        )
    ).json()

    assert body["citations"] == [], "The only matching document belongs to another project."


async def test_the_trace_is_persisted_and_retrievable(session, project, client) -> None:
    """The trace endpoint reads from Postgres, so it only works if the query run was written."""
    await _seed_commit(
        session,
        external_id="sha-1",
        title="Harden the deployment rollback",
        content="Reworked the deployment rollback so a failed migration reverts cleanly.",
    )
    body = (
        await client.post(
            "/query",
            headers=HEADERS,
            json={"query": "What deployment rollback work happened?", "project_id": "test-project"},
        )
    ).json()

    trace = await client.get(f"/conversations/{body['conversation_id']}/trace", headers=HEADERS)

    assert trace.status_code == 200
    steps = trace.json()["trace"]
    assert [step["name"] for step in steps] == [step["name"] for step in body["trace"]]
    # Durations are measured, not the fabricated 42/118/430/86/55 literals they once were.
    assert all(step["duration_ms"] >= 0 for step in steps)


async def test_query_still_requires_an_api_key(session, project, client) -> None:
    response = await client.post("/query", json={"query": "anything", "project_id": "test-project"})

    assert response.status_code == 401
