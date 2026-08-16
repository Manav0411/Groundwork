from app.connectors.github import GitHubCommit
from app.connectors.jira import JiraIssue, JiraUser
from app.services.ingestion import chunk_text, github_commit_documents, jira_issue_documents


def test_chunk_text_preserves_short_content() -> None:
    assert chunk_text("  A short   project update. ") == ["A short project update."]


def test_chunk_text_splits_long_content_with_overlap() -> None:
    chunks = chunk_text("word " * 600, max_chars=200, overlap_chars=20)

    assert len(chunks) > 2
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_github_commit_becomes_retrievable_document() -> None:
    documents = github_commit_documents(
        "project-atlas",
        [
            GitHubCommit(
                sha="abc123def456",
                message="Fix ingestion race\n\nPreserve idempotency.",
                author="Raghav",
                author_email="raghav@example.com",
                author_login="raghav-dev",
                committer="Raghav",
                authored_at="2026-08-09T09:58:00Z",
                committed_at="2026-08-09T10:00:00Z",
                url="https://github.com/acme/project/commit/abc123def456",
            )
        ],
    )

    assert documents[0].external_id == "abc123def456"
    assert "Raghav" in documents[0].content
    assert documents[0].source_created_at is not None
    assert documents[0].author_identities == [
        "raghav",
        "raghav-dev",
        "raghav@example.com",
    ]


def test_database_metadata_contains_persistence_tables() -> None:
    from app.db import models  # noqa: F401
    from app.db.base import Base

    assert {
        "projects",
        "source_documents",
        "connector_sync_states",
        "document_chunks",
        "conversations",
        "query_runs",
        "query_citations",
        "retrieved_evidence",
        "trace_steps",
    }.issubset(Base.metadata.tables)


def test_jira_issue_becomes_normalized_retrievable_document() -> None:
    user = JiraUser(display_name="Manav Goel", account_id="account-1", email=None)
    documents = jira_issue_documents(
        "askbase",
        [
            JiraIssue(
                key="ASK-6",
                summary="Investigate Ollama memory requirements on EC2",
                description="Measure memory usage.",
                status="To Do",
                status_category="new",
                priority="Highest",
                issue_type="Task",
                assignee=user,
                reporter=user,
                labels=["blocked"],
                comments=["Manav Goel: Waiting for instance sizing."],
                created_at="2026-08-16T10:00:00Z",
                updated_at="2026-08-16T11:00:00Z",
                url="https://jira.test/browse/ASK-6",
            )
        ],
    )

    document = documents[0]
    assert document.external_id == "ASK-6"
    assert document.source_type == "jira"
    assert document.author_identities == ["account-1", "manav goel"]
    assert document.metadata["status"] == "To Do"
    assert document.metadata["priority"] == "Highest"
    assert "Waiting for instance sizing" in document.content
