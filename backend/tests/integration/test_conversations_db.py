"""Multi-turn conversations against a real database.

Two things are only observable with Postgres present: that a conversation is appended to rather
than recreated, and that history never leaks into evidence. The second is the invariant this phase
had to protect — if a prior answer can become a later answer's source, one hallucination becomes
self-reinforcing.
"""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.agent import nodes
from app.core.config import settings
from app.db.models import Conversation, QueryRun
from app.db.session import get_optional_session, get_session
from app.main import app
from app.models.schemas import ConversationTurn
from app.services.ingestion import IngestDocument, ingest_documents
from app.services.persistence import (
    ConversationNotFoundError,
    load_conversation_history,
)

pytestmark = pytest.mark.integration

HEADERS = {"X-API-Key": settings.app_api_key}


@pytest_asyncio.fixture
async def client(session):
    async def _override():
        yield session

    app.dependency_overrides[get_optional_session] = _override
    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()


@pytest.fixture
def resolver(monkeypatch):
    """Replace the model with a scripted rewrite, so resolution is exact rather than probabilistic.

    Model quality is the live evals' job. What is asserted here is that a resolved question reaches
    the router and changes which path answers it.
    """
    calls: list[tuple[str, list[ConversationTurn]]] = []
    script: dict[str, str] = {}

    async def _fake(query, history, ollama=None):
        calls.append((query, list(history)))
        return script.get(query)

    monkeypatch.setattr(nodes, "resolve_followup", _fake)
    return type("Resolver", (), {"calls": calls, "script": script})()


async def _seed_jira(session) -> None:
    await ingest_documents(
        session,
        [
            IngestDocument(
                project_id="test-project",
                source_type="jira",
                external_id="TEST-6",
                title="TEST-6 — Deploy to EC2",
                content=(
                    "Jira issue TEST-6: Deploy to EC2. "
                    "Status: In Review. Assignee: Manav Goel."
                ),
                url="https://acme.atlassian.net/browse/TEST-6",
                metadata={
                    "key": "TEST-6",
                    "summary": "Deploy to EC2",
                    "status": "In Review",
                    "status_category": "indeterminate",
                    "priority": "High",
                    "issue_type": "Task",
                    "assignee": "Manav Goel",
                },
            )
        ],
        None,
    )


async def _ask(client, query: str, conversation_id: str | None = None) -> dict:
    payload = {"query": query, "project_id": "test-project"}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    response = await client.post("/query", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_conversation_is_appended_to_not_recreated(session, project, client) -> None:
    """Before this phase every conversation was exactly one question long."""
    await _seed_jira(session)

    first = await _ask(client, "What is the status of TEST-6?")
    second = await _ask(client, "What is the status of TEST-6?", first["conversation_id"])

    assert second["conversation_id"] == first["conversation_id"]
    conversations = list((await session.scalars(select(Conversation))).all())
    assert len(conversations) == 1
    runs = list((await session.scalars(select(QueryRun))).all())
    assert len(runs) == 2


async def test_a_follow_up_reaches_the_deterministic_jira_path(
    session, project, client, resolver
) -> None:
    """The point of the phase: a pronoun question answered by exact SQL, not generic retrieval."""
    await _seed_jira(session)
    resolver.script["Who is it assigned to?"] = "Who is TEST-6 assigned to?"

    first = await _ask(client, "What is the status of TEST-6?")
    second = await _ask(client, "Who is it assigned to?", first["conversation_id"])

    assert second["resolved_query"] == "Who is TEST-6 assigned to?"
    assert "Manav Goel" in second["answer"]
    run = await session.scalar(select(QueryRun).where(QueryRun.query == "Who is it assigned to?"))
    # `jira_issue_status`, not `jira_assignee`: the resolved question carries an issue key, and
    # routing orders by specificity — a key names one record and outranks an assignee clause.
    # Unresolved, this question carries no identifier at all and would have gone to retrieval.
    assert run.query_type == "jira_issue_status"
    # What the user typed is preserved; the standalone form is stored beside it.
    assert run.query == "Who is it assigned to?"
    assert run.resolved_query == "Who is TEST-6 assigned to?"


async def test_a_self_contained_question_never_reaches_the_resolver(
    session, project, client, resolver
) -> None:
    """The gate must keep the exact-answer paths free of any model dependency."""
    await _seed_jira(session)

    first = await _ask(client, "What is the status of TEST-6?")
    await _ask(client, "What is the status of TEST-6?", first["conversation_id"])

    assert resolver.calls == []


async def test_resolution_failure_answers_the_original_question(
    session, project, client, resolver
) -> None:
    """Ollama down must degrade, not fail. The scripted resolver returns None for this query."""
    await _seed_jira(session)

    first = await _ask(client, "What is the status of TEST-6?")
    second = await _ask(client, "Who is it assigned to?", first["conversation_id"])

    assert second["resolved_query"] is None
    assert second["answer"]
    assert any("Follow-up Resolution" == step["name"] for step in second["trace"])


async def test_history_is_capped_and_ordered_oldest_first(session, project, client) -> None:
    await _seed_jira(session)
    conversation_id = (await _ask(client, "What is the status of TEST-6?"))["conversation_id"]
    for index in range(6):
        await _ask(client, f"What is the status of TEST-6? ({index})", conversation_id)

    history = await load_conversation_history(session, conversation_id, "test-project", limit=3)

    assert len(history) == 3
    assert [turn.query for turn in history] == [
        "What is the status of TEST-6? (3)",
        "What is the status of TEST-6? (4)",
        "What is the status of TEST-6? (5)",
    ]


async def test_a_prior_answer_never_becomes_evidence(session, project, client, resolver) -> None:
    """The invariant. History informs resolution only; it must not be citable.

    Turn 1's answer contains "Manav Goel". If history leaked into retrieval, turn 2 could cite the
    answer itself rather than the Jira document, and a hallucinated turn would become a source.
    """
    await _seed_jira(session)
    resolver.script["Who is it assigned to?"] = "Who is TEST-6 assigned to?"

    first = await _ask(client, "What is the status of TEST-6?")
    second = await _ask(client, "Who is it assigned to?", first["conversation_id"])

    citation_titles = [citation["title"] for citation in second["citations"]]
    snippets = [item["snippet"] for item in second["evidence"]]
    assert all(title.startswith("TEST-6") for title in citation_titles)
    assert all(first["answer"] not in snippet for snippet in snippets)
    # Every citation still points at an indexed document, never at a conversation turn.
    assert all(
        citation["source_type"] in {"jira", "github", "slack", "web"}
        for citation in second["citations"]
    )


async def test_an_unknown_conversation_id_is_rejected(session, project, client) -> None:
    """Silently starting over would look like the agent had forgotten the thread."""
    response = await client.post(
        "/query",
        headers=HEADERS,
        json={
            "query": "Who is it assigned to?",
            "project_id": "test-project",
            "conversation_id": "conv-does-not-exist",
        },
    )

    assert response.status_code == 404


async def test_a_conversation_cannot_cross_projects(
    session, project, other_project, client
) -> None:
    """An id leaking across projects would let one project's history steer another's answers."""
    await _seed_jira(session)
    conversation_id = (await _ask(client, "What is the status of TEST-6?"))["conversation_id"]

    response = await client.post(
        "/query",
        headers=HEADERS,
        json={
            "query": "Who is it assigned to?",
            "project_id": "other-project",
            "conversation_id": conversation_id,
        },
    )

    assert response.status_code == 404
    with pytest.raises(ConversationNotFoundError):
        await load_conversation_history(session, conversation_id, "other-project")


async def test_the_turns_endpoint_restores_the_thread(session, project, client) -> None:
    await _seed_jira(session)
    first = await _ask(client, "What is the status of TEST-6?")
    await _ask(client, "What is the status of TEST-6? (again)", first["conversation_id"])

    response = await client.get(
        f"/conversations/{first['conversation_id']}",
        headers=HEADERS,
        params={"project_id": "test-project"},
    )

    assert response.status_code == 200
    turns = response.json()["turns"]
    assert [turn["query"] for turn in turns] == [
        "What is the status of TEST-6?",
        "What is the status of TEST-6? (again)",
    ]


async def test_the_first_turn_records_no_resolution_attempt(session, project, client) -> None:
    await _seed_jira(session)

    body = await _ask(client, "What is the status of TEST-6?")

    assert body["resolved_query"] is None
    step = next(item for item in body["trace"] if item["name"] == "Follow-up Resolution")
    assert "First turn" in step["summary"]
