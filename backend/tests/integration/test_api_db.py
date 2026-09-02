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


async def test_an_exact_commit_answer_is_graded_correct(session, project, client) -> None:
    """Guards a regression the unit tests could not see.

    Adding the out-of-range branch to the GitHub node displaced `grade = "correct"` into it, so
    every successful exact answer silently came back `ambiguous`. Content, SHA, and citation were
    all still right, which is why only the grade revealed it — and only the live eval gate caught
    it. This asserts the grade directly.
    """
    from datetime import UTC, datetime

    from app.connectors.github import GitHubCommit
    from app.db.models import ConnectorSyncState
    from app.services.ingestion import github_commit_documents

    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type="github",
            status="succeeded",
            last_succeeded_at=datetime.now(UTC),
        )
    )
    await ingest_documents(
        session,
        github_commit_documents(
            "test-project",
            [
                GitHubCommit(
                    sha="f4a941f777055b47be553f28115dac1fa5018d93",
                    message="Refactor README for better structure and clarity",
                    author="Manav Goel",
                    author_email="manav@example.com",
                    author_login="Manav0411",
                    committer="Manav Goel",
                    authored_at="2026-05-11T14:38:59Z",
                    committed_at="2026-05-11T14:38:59Z",
                    url="https://github.com/acme/test/commit/f4a941f",
                )
            ],
        ),
        None,
    )

    body = (
        await client.post(
            "/query",
            headers=HEADERS,
            json={"query": "What was the last commit by Manav0411?", "project_id": "test-project"},
        )
    ).json()

    assert body["retrieval_grade"] == "correct"
    assert body["unresolved_gaps"] == []
    assert len(body["citations"]) == 1
    assert "f4a941f" in body["answer"]


async def test_a_quantifier_question_is_counted_and_graded_correct(
    session, project, client
) -> None:
    """The aggregate limitation, end to end through the HTTP surface.

    Asserts the grade explicitly for the same reason the exact-commit test does: a new branch in a
    structured node is exactly where `grade = "correct"` gets displaced, and content can look
    perfect while the grade is wrong.
    """
    from datetime import UTC, datetime

    from app.connectors.jira import JiraIssue, JiraUser
    from app.db.models import ConnectorSyncState
    from app.services.ingestion import jira_issue_documents

    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type="jira",
            status="succeeded",
            last_succeeded_at=datetime.now(UTC),
        )
    )

    def issue(key: str, status: str, category: str) -> JiraIssue:
        user = JiraUser(display_name="Raghav Rao", account_id="acct-1", email=None)
        return JiraIssue(
            key=key,
            summary=f"Summary for {key}",
            description="Description text.",
            status=status,
            status_category=category,
            priority="Medium",
            issue_type="Task",
            assignee=user,
            reporter=user,
            labels=[],
            comments=[],
            created_at="2026-07-01T09:00:00Z",
            updated_at="2026-08-01T09:00:00Z",
            url=f"https://acme.atlassian.net/browse/{key}",
        )

    await ingest_documents(
        session,
        jira_issue_documents(
            "test-project",
            [
                issue("TEST-1", "Done", "done"),
                issue("TEST-2", "In Progress", "indeterminate"),
            ],
        ),
        None,
    )
    await session.commit()

    response = await client.post(
        "/query",
        headers=HEADERS,
        json={"query": "Are all the tasks complete?", "project_id": "test-project"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query_type"] == "jira_project_status"
    assert body["retrieval_grade"] == "correct"
    assert "1 of 2" in body["answer"]
    assert "TEST-2" in body["answer"]
    # The outstanding work is cited, so the count is auditable rather than asserted.
    assert body["citations"]


async def test_a_background_sync_is_accepted_and_pollable(session, project, client) -> None:
    """The point of the flag: return before the work is done, and say where to look.

    The connector will fail without credentials, which is fine and is the interesting half -- the
    request must still come back immediately rather than propagating that failure to a caller who
    has already been told the work was accepted.
    """
    response = await client.post(
        "/projects/test-project/sync/github?background=true", headers=HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["poll"] == "/projects/test-project/sync/github"

    # And the status endpoint it points at actually exists.
    status = await client.get(body["poll"], headers=HEADERS)
    assert status.status_code == 200


async def test_a_background_sync_rejects_an_unknown_project_up_front(session, client) -> None:
    """A 202 for work that was never going to happen is worse than a slow response.

    The caller would poll a status that never changes, with nothing to explain why.
    """
    response = await client.post(
        "/projects/does-not-exist/sync/github?background=true", headers=HEADERS
    )

    assert response.status_code == 404


async def test_a_background_sync_refuses_to_stack_on_a_running_one(
    session, project, client
) -> None:
    """The in-progress guard already exists in the sync services; this checks it is reached
    before scheduling rather than after, when it would be too late to tell the caller."""
    from datetime import UTC, datetime

    from app.db.models import ConnectorSyncState

    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type="github",
            status="running",
            last_started_at=datetime.now(UTC),
        )
    )
    await session.commit()

    response = await client.post(
        "/projects/test-project/sync/github?background=true", headers=HEADERS
    )

    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


async def test_the_default_is_still_synchronous(session, project, client) -> None:
    """Flipping the default would leave the eval gates passing against stale data.

    The harness posts a sync and queries immediately; a silent 202 would mean it measured whatever
    was there before. That is worse than failing, because nothing would say so.
    """
    import inspect

    from app.api import routes

    signature = inspect.signature(routes.sync_project_github)

    assert signature.parameters["background"].default is False


async def test_an_exact_slack_answer_is_graded_correct(session, project, client) -> None:
    """The whole Slack path, end to end, with no model call anywhere in it.

    Written against the same regression the commit test guards: this node also
    has an out-of-range branch, and misplacing `grade = "correct"` into it would
    leave every successful answer silently `ambiguous` with the content, the
    citation and the trace all still correct.

    The question is the one that exposed the gap in production. It reached
    hybrid retrieval, where ranking is semantic and "the last conversation" has
    no semantics to rank on, and was refused after two corrective attempts.
    """
    from datetime import UTC, datetime

    from app.connectors.slack import SlackMessage, SlackThread
    from app.db.models import ConnectorSyncState
    from app.services.ingestion import slack_thread_documents

    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type="slack",
            status="succeeded",
            last_succeeded_at=datetime.now(UTC),
        )
    )
    await ingest_documents(
        session,
        slack_thread_documents(
            "test-project",
            [
                SlackThread(
                    channel_id="C0BRTQ9BZ7H",
                    channel_name="groundwork-eng",
                    thread_ts="1756000000.000100",
                    messages=[
                        SlackMessage(
                            ts="1756000000.000100",
                            user_id="U1",
                            author="Manav Goel",
                            text="Recall fell to 0.717 on the larger grader",
                        )
                    ],
                    permalink="https://acme.slack.com/archives/C0BRTQ9BZ7H/p1756000000000100",
                ),
                # Older, so it must not win.
                SlackThread(
                    channel_id="C0BRTQ9BZ7H",
                    channel_name="groundwork-eng",
                    thread_ts="1755000000.000100",
                    messages=[
                        SlackMessage(
                            ts="1755000000.000100",
                            user_id="U2",
                            author="Riya S",
                            text="Kicking off the deployment work",
                        )
                    ],
                    permalink="https://acme.slack.com/archives/C0BRTQ9BZ7H/p1755000000000100",
                ),
            ],
        ),
        None,
    )

    body = (
        await client.post(
            "/query",
            headers=HEADERS,
            json={
                "query": "What was the last conversation on slack?",
                "project_id": "test-project",
            },
        )
    ).json()

    assert body["retrieval_grade"] == "correct"
    assert body["unresolved_gaps"] == []
    assert len(body["citations"]) == 1
    assert "Recall fell to 0.717" in body["answer"]
    assert "structured_slack_query" in body["tools_used"]
    # The exact-answer contract: no grading, no synthesis, no model.
    assert not any("Grader" in step["name"] for step in body["trace"])
