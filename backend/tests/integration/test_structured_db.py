"""Deterministic SQL tools against a real database.

These are the tools that answer exact questions — latest commit, ticket status, blockers — and the
design rule they exist to enforce is that they never fall back to semantic guessing. An ambiguous
match must produce an ambiguity response, not a best guess. Ordering ("latest") and refusal
("ambiguous") are both properties of the query, so neither is observable without a database.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.connectors.github import GitHubCommit
from app.connectors.jira import JiraIssue, JiraUser
from app.db.models import ConnectorSyncState
from app.services.ingestion import (
    github_commit_documents,
    ingest_documents,
    jira_issue_documents,
)
from app.services.structured_github import latest_commit_by_author
from app.services.structured_jira import (
    jira_issue_by_key,
    jira_issues_by_assignee,
    open_jira_blockers,
)

pytestmark = pytest.mark.integration


def _commit(sha: str, author: str, *, login: str | None = None, at: str) -> GitHubCommit:
    return GitHubCommit(
        sha=sha,
        message=f"Work in {sha}",
        author=author,
        author_email=f"{(login or author).lower().replace(' ', '.')}@example.com",
        author_login=login,
        committer=author,
        authored_at=at,
        committed_at=at,
        url=f"https://github.com/acme/test/commit/{sha}",
    )


def _issue(
    key: str,
    *,
    status: str = "In Progress",
    category: str = "indeterminate",
    priority: str | None = "Medium",
    assignee: str | None = "Raghav Rao",
    labels: list[str] | None = None,
    updated: str = "2026-08-01T09:00:00Z",
) -> JiraIssue:
    user = (
        JiraUser(display_name=assignee, account_id=f"acct-{assignee}", email=None)
        if assignee
        else None
    )
    return JiraIssue(
        key=key,
        summary=f"Summary for {key}",
        description="Description text.",
        status=status,
        status_category=category,
        priority=priority,
        issue_type="Task",
        assignee=user,
        reporter=user,
        labels=labels or [],
        comments=[],
        created_at="2026-07-01T09:00:00Z",
        updated_at=updated,
        url=f"https://acme.atlassian.net/browse/{key}",
    )


async def _ingest_commits(session, commits: list[GitHubCommit]) -> None:
    await ingest_documents(session, github_commit_documents("test-project", commits), None)


async def _ingest_issues(session, issues: list[JiraIssue]) -> None:
    await ingest_documents(session, jira_issue_documents("test-project", issues), None)


async def _mark_synced(session, source_type: str, *, minutes_ago: int = 0) -> None:
    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type=source_type,
            status="succeeded",
            last_succeeded_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        )
    )
    await session.flush()


# --- GitHub -----------------------------------------------------------------------------------


async def test_latest_commit_returns_the_newest_not_merely_a_match(session, project) -> None:
    """Ordering is the entire reason this path exists instead of embedding similarity."""
    await _ingest_commits(
        session,
        [
            _commit("old", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z"),
            _commit("newest", "Raghav Rao", login="raghav-dev", at="2026-08-20T09:00:00Z"),
            _commit("middle", "Raghav Rao", login="raghav-dev", at="2026-08-10T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "Raghav Rao")

    assert lookup.status == "found"
    assert lookup.sha == "newest"


async def test_author_matching_is_case_and_spacing_insensitive(session, project) -> None:
    """Identities are normalized at ingestion so the exact tool never scans JSON metadata."""
    await _ingest_commits(
        session, [_commit("a", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(session, "test-project", "  RAGHAV   rao ")

    assert lookup.status == "found"
    assert lookup.sha == "a"


async def test_a_login_also_resolves_the_author(session, project) -> None:
    await _ingest_commits(
        session, [_commit("a", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z")]
    )

    assert (await latest_commit_by_author(session, "test-project", "raghav-dev")).sha == "a"


async def test_two_people_sharing_a_prefix_are_ambiguous_not_guessed(session, project) -> None:
    """The rule: no best guess. A wrong confident answer is worse than an admitted ambiguity."""
    await _ingest_commits(
        session,
        [
            _commit("a", "Raghav Rao", login="raghav-rao", at="2026-08-01T09:00:00Z"),
            _commit("b", "Raghav Menon", login="raghav-menon", at="2026-08-02T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "raghav")

    assert lookup.status == "ambiguous"
    assert lookup.sha is None
    assert lookup.record is None
    assert lookup.candidates == ["Raghav Menon", "Raghav Rao"]


async def test_one_person_under_two_spellings_still_resolves(session, project) -> None:
    """A partial match that names exactly one human is an answer, not an ambiguity."""
    await _ingest_commits(
        session,
        [
            _commit("a", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z"),
            _commit("b", "Raghav Rao", login="raghav-rao", at="2026-08-05T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "raghav r")

    assert lookup.status == "found"
    assert lookup.author == "Raghav Rao"


async def test_unknown_author_is_not_found_with_no_candidates(session, project) -> None:
    await _ingest_commits(
        session, [_commit("a", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(session, "test-project", "Someone Else")

    assert lookup.status == "not_found"
    assert lookup.candidates == []


async def test_commit_lookup_is_scoped_to_the_project(session, project, other_project) -> None:
    await ingest_documents(
        session,
        github_commit_documents(
            "other-project", [_commit("a", "Raghav Rao", login="r", at="2026-08-01T09:00:00Z")]
        ),
        None,
    )

    assert (
        await latest_commit_by_author(session, "test-project", "Raghav Rao")
    ).status == "not_found"


async def test_freshness_reports_stale_when_never_synced(session, project) -> None:
    """Freshness policy downgrades otherwise-correct answers, so it must be derived, not assumed."""
    await _ingest_commits(
        session, [_commit("a", "Raghav Rao", login="r", at="2026-08-01T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(session, "test-project", "Raghav Rao")

    assert lookup.last_synced_at is None
    assert lookup.stale is True


async def test_a_recent_sync_is_not_stale(session, project) -> None:
    await _mark_synced(session, "github")
    await _ingest_commits(
        session, [_commit("a", "Raghav Rao", login="r", at="2026-08-01T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(session, "test-project", "Raghav Rao")

    assert lookup.last_synced_at is not None
    assert lookup.stale is False


# --- Jira -------------------------------------------------------------------------------------


async def test_issue_lookup_by_key_is_case_insensitive(session, project) -> None:
    await _ingest_issues(session, [_issue("TEST-6", status="In Review")])

    lookup = await jira_issue_by_key(session, "test-project", "test-6")

    assert lookup.status == "found"
    (issue,) = lookup.issues
    assert (issue.key, issue.status) == ("TEST-6", "In Review")
    assert issue.record.url.endswith("/browse/TEST-6")


async def test_unknown_issue_key_is_not_found(session, project) -> None:
    await _ingest_issues(session, [_issue("TEST-1")])

    assert (await jira_issue_by_key(session, "test-project", "TEST-999")).status == "not_found"


async def test_blockers_match_priority_or_label(session, project) -> None:
    """The project's definition: open AND (priority Highest/Blocker OR a `blocked` label)."""
    await _ingest_issues(
        session,
        [
            _issue("TEST-1", priority="Highest"),
            _issue("TEST-2", priority="Low", labels=["blocked"]),
            _issue("TEST-3", priority="Blocker"),
            _issue("TEST-4", priority="Low"),
        ],
    )

    lookup = await open_jira_blockers(session, "test-project")

    assert {issue.key for issue in lookup.issues} == {"TEST-1", "TEST-2", "TEST-3"}


async def test_closed_issues_are_never_blockers(session, project) -> None:
    """`status_category = done` disqualifies an issue however alarming its priority looks."""
    await _ingest_issues(
        session,
        [
            _issue("TEST-1", priority="Highest", status="Done", category="done"),
            _issue("TEST-2", priority="Highest"),
        ],
    )

    lookup = await open_jira_blockers(session, "test-project")

    assert [issue.key for issue in lookup.issues] == ["TEST-2"]


async def test_no_blockers_reports_not_found_rather_than_an_empty_success(session, project) -> None:
    await _ingest_issues(session, [_issue("TEST-1", priority="Low")])

    lookup = await open_jira_blockers(session, "test-project")

    assert lookup.status == "not_found"
    assert lookup.issues == []


async def test_assignee_lookup_returns_every_matching_issue_newest_first(session, project) -> None:
    await _ingest_issues(
        session,
        [
            _issue("TEST-1", assignee="Raghav Rao", updated="2026-08-01T09:00:00Z"),
            _issue("TEST-2", assignee="Raghav Rao", updated="2026-08-15T09:00:00Z"),
            _issue("TEST-3", assignee="Sarah Kim", updated="2026-08-20T09:00:00Z"),
        ],
    )

    lookup = await jira_issues_by_assignee(session, "test-project", "Raghav Rao")

    assert lookup.status == "found"
    assert [issue.key for issue in lookup.issues] == ["TEST-2", "TEST-1"]


async def test_ambiguous_assignee_yields_candidates_and_no_issues(session, project) -> None:
    await _ingest_issues(
        session,
        [
            _issue("TEST-1", assignee="Raghav Rao"),
            _issue("TEST-2", assignee="Raghav Menon"),
        ],
    )

    lookup = await jira_issues_by_assignee(session, "test-project", "raghav")

    assert lookup.status == "ambiguous"
    assert lookup.issues == []
    assert lookup.candidates == ["Raghav Menon", "Raghav Rao"]


async def test_unassigned_issues_carry_no_identities(session, project) -> None:
    await _ingest_issues(session, [_issue("TEST-1", assignee=None)])

    lookup = await jira_issues_by_assignee(session, "test-project", "anyone")

    assert lookup.status == "not_found"


async def test_jira_metadata_survives_the_round_trip(session, project) -> None:
    """The structured tool reads JSONB written at ingestion; a shape change breaks it silently."""
    await _ingest_issues(
        session, [_issue("TEST-9", status="Blocked", priority="Highest", labels=["blocked"])]
    )

    (issue,) = (await jira_issue_by_key(session, "test-project", "TEST-9")).issues

    assert issue.summary == "Summary for TEST-9"
    assert issue.priority == "Highest"
    assert issue.assignee == "Raghav Rao"
    assert issue.issue_type == "Task"
    assert issue.record.authority == 0.95
