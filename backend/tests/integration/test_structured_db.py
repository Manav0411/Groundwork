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
from app.connectors.slack import SlackMessage, SlackThread
from app.core.config import settings
from app.db.models import ConnectorSyncState
from app.services.ingestion import (
    github_commit_documents,
    ingest_documents,
    jira_issue_documents,
    slack_thread_documents,
)
from app.services.structured_github import (
    commit_by_sha,
    latest_commit_by_author,
)
from app.services.structured_jira import (
    jira_issue_by_key,
    jira_issues_by_assignee,
    jira_project_status,
    open_jira_blockers,
)
from app.services.structured_slack import latest_slack_threads

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
    account_id: str | None = None,
    labels: list[str] | None = None,
    updated: str = "2026-08-01T09:00:00Z",
) -> JiraIssue:
    # `account_id` defaults to one derived from the display name, so two spellings of the same
    # person can be given the same account by passing it explicitly.
    user = (
        JiraUser(
            display_name=assignee, account_id=account_id or f"acct-{assignee}", email=None
        )
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


async def test_commit_offset_returns_the_nth_newest_not_the_newest(session, project) -> None:
    """The defect: "second-to-last commit by X" ran the same lookup as "last commit by X"."""
    await _ingest_commits(
        session,
        [
            _commit("newest", "Raghav Rao", login="raghav-dev", at="2026-08-20T09:00:00Z"),
            _commit("middle", "Raghav Rao", login="raghav-dev", at="2026-08-10T09:00:00Z"),
            _commit("oldest", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z"),
        ],
    )

    async def sha_at(offset: int) -> str | None:
        return (await latest_commit_by_author(session, "test-project", "Raghav Rao", offset)).sha

    assert await sha_at(0) == "newest"
    assert await sha_at(1) == "middle"
    assert await sha_at(2) == "oldest"


async def test_an_offset_past_the_history_refuses_rather_than_substituting(
    session, project
) -> None:
    """Returning the newest commit for a position that does not exist is confidently wrong."""
    await _ingest_commits(
        session, [_commit("only", "Raghav Rao", login="raghav-dev", at="2026-08-20T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(session, "test-project", "Raghav Rao", 3)

    assert lookup.status == "out_of_range"
    assert lookup.record is None
    assert lookup.sha is None
    assert lookup.available == 1


async def test_offset_applies_to_a_partial_author_match_too(session, project) -> None:
    await _ingest_commits(
        session,
        [
            _commit("newest", "Raghav Rao", login="raghav-dev", at="2026-08-20T09:00:00Z"),
            _commit("older", "Raghav Rao", login="raghav-rao", at="2026-08-01T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "raghav r", 1)

    assert lookup.status == "found"
    assert lookup.sha == "older"


async def test_a_named_hash_anchors_the_count_instead_of_being_the_answer(
    session, project
) -> None:
    """"What came before f4a941f?" counts from that commit, not from the top of the list."""
    await _ingest_commits(
        session,
        [
            _commit("aaa1111", "Raghav Rao", login="raghav-dev", at="2026-08-20T09:00:00Z"),
            _commit("bbb2222", "Raghav Rao", login="raghav-dev", at="2026-08-10T09:00:00Z"),
            _commit("ccc3333", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(
        session, "test-project", "Raghav Rao", 1, anchor_sha="bbb2222"
    )

    assert lookup.sha == "ccc3333"
    # The absolute position, so the wording matches the list rather than the step from the anchor.
    assert lookup.offset == 2


async def test_an_unknown_anchor_refuses_rather_than_counting_from_the_top(
    session, project
) -> None:
    """Otherwise "the commit before <unknown>" silently becomes "the commit before the newest"."""
    await _ingest_commits(
        session, [_commit("aaa1111", "Raghav Rao", login="raghav-dev", at="2026-08-20T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(
        session, "test-project", "Raghav Rao", 1, anchor_sha="deadbee1"
    )

    assert lookup.status == "not_found"
    assert lookup.record is None


async def test_a_commit_is_found_by_hash_prefix(session, project) -> None:
    """The path that previously went through a 3B model and got contradicted by its own evidence."""
    await _ingest_commits(
        session,
        [_commit("f4a941f777055b", "Manav Goel", login="Manav0411", at="2026-05-11T14:38:59Z")],
    )

    lookup = await commit_by_sha(session, "test-project", "f4a941f")

    assert lookup.status == "found"
    assert lookup.author == "Manav Goel"
    assert lookup.record is not None
    assert lookup.record.source_timestamp is not None


async def test_an_ambiguous_hash_prefix_refuses(session, project) -> None:
    """Two commits sharing a prefix must not resolve to whichever happens to sort first."""
    await _ingest_commits(
        session,
        [
            _commit("abc1234aaa", "Manav Goel", login="Manav0411", at="2026-05-11T14:38:59Z"),
            _commit("abc1234bbb", "Manav Goel", login="Manav0411", at="2026-05-10T14:38:59Z"),
        ],
    )

    lookup = await commit_by_sha(session, "test-project", "abc1234")

    assert lookup.status == "ambiguous"
    assert lookup.record is None


async def test_an_unknown_hash_is_not_found(session, project) -> None:
    await _ingest_commits(
        session, [_commit("aaa1111", "Manav Goel", login="Manav0411", at="2026-05-11T14:38:59Z")]
    )

    assert (await commit_by_sha(session, "test-project", "deadbee1")).status == "not_found"


async def test_project_status_counts_the_whole_set(session, project) -> None:
    """The aggregate limitation, answered by counting instead of by retrieval.

    "Are all the tasks complete?" used to be refused: the grader judges whether a *passage*
    supports the answer, and no single chunk states a fact about the set. Every chunk was rejected,
    which was the grader working correctly on a question retrieval cannot answer.
    """
    await _ingest_issues(
        session,
        [
            _issue("TEST-1", status="Done", category="done"),
            _issue("TEST-2", status="Done", category="done"),
            _issue("TEST-3", status="In Progress", category="indeterminate"),
            _issue("TEST-4", status="To Do", category="new"),
        ],
    )

    status = await jira_project_status(session, "test-project")

    assert status.total == 4
    assert status.done == 2
    assert status.complete is False
    assert {issue.key for issue in status.outstanding} == {"TEST-3", "TEST-4"}


async def test_project_status_reports_completion_only_when_everything_is_done(
    session, project
) -> None:
    await _ingest_issues(
        session,
        [
            _issue("TEST-1", status="Done", category="done"),
            _issue("TEST-2", status="Closed", category="Done"),  # category casing varies by site
        ],
    )

    status = await jira_project_status(session, "test-project")

    assert status.total == 2
    assert status.done == 2
    assert status.complete is True
    assert status.outstanding == []


async def test_an_empty_project_is_not_a_complete_one(session, project) -> None:
    """Vacuous truth is the wrong answer here.

    "All zero issues are done" would be a confident yes for a project nobody has synced, which is
    indistinguishable from a project that really is finished.
    """
    status = await jira_project_status(session, "test-project")

    assert status.total == 0
    assert status.complete is False


# --- Slack ------------------------------------------------------------------------------------


def _thread(
    channel: str,
    thread_ts: str,
    *,
    headline: str,
    author: str = "Manav Goel",
    replies: list[tuple[str, str, str]] | None = None,
) -> SlackThread:
    """`replies` is (ts, author, text); the newest ts decides the thread's recency."""
    messages = [SlackMessage(ts=thread_ts, user_id="U1", author=author, text=headline)]
    for ts, reply_author, text in replies or []:
        messages.append(SlackMessage(ts=ts, user_id="U2", author=reply_author, text=text))
    return SlackThread(
        channel_id=f"C{channel.upper()}",
        channel_name=channel,
        thread_ts=thread_ts,
        messages=messages,
        permalink=f"https://acme.slack.com/archives/C{channel.upper()}/p{thread_ts}",
    )


async def _ingest_threads(session, threads: list[SlackThread]) -> None:
    await ingest_documents(session, slack_thread_documents("test-project", threads), None)


async def test_latest_thread_is_the_most_recently_active_not_the_newest_started(
    session, project
) -> None:
    """The ordering rule that makes this tool worth having.

    A thread started earlier but replied to today is more recent than one
    started later and abandoned. Ordering by start time would return the wrong
    row, and no unit test can observe that.
    """
    await _ingest_threads(
        session,
        [
            # Started first, still being replied to.
            _thread(
                "eng",
                "1000000000.000100",
                headline="Grader model choice",
                replies=[("1000200000.000100", "Riya", "Recall dropped to 0.717")],
            ),
            # Started later, never touched again.
            _thread("eng", "1000100000.000200", headline="Lunch plans"),
        ],
    )
    await _mark_synced(session, "slack")

    lookup = await latest_slack_threads(session, "test-project")

    assert lookup.status == "found"
    assert lookup.available == 2
    assert lookup.threads[0].headline == "Grader model choice"
    assert lookup.threads[0].message_count == 2


async def test_channel_filter_scopes_the_lookup(session, project) -> None:
    await _ingest_threads(
        session,
        [
            _thread("eng", "1000300000.000100", headline="Newest overall, wrong channel"),
            _thread("ops", "1000200000.000100", headline="Newest in ops"),
        ],
    )
    await _mark_synced(session, "slack")

    scoped = await latest_slack_threads(session, "test-project", channel="ops")

    assert scoped.status == "found"
    assert scoped.available == 1
    assert scoped.threads[0].headline == "Newest in ops"
    assert scoped.threads[0].channel_name == "ops"


async def test_offset_walks_back_through_the_thread_list(session, project) -> None:
    await _ingest_threads(
        session,
        [
            _thread("eng", "1000300000.000100", headline="Third"),
            _thread("eng", "1000200000.000100", headline="Second"),
            _thread("eng", "1000100000.000100", headline="First"),
        ],
    )
    await _mark_synced(session, "slack")

    assert (await latest_slack_threads(session, "test-project", offset=1)).threads[
        0
    ].headline == "Second"
    beyond = await latest_slack_threads(session, "test-project", offset=9)
    assert beyond.status == "out_of_range"
    # The count is what lets the answer say how many actually exist.
    assert beyond.available == 3


async def test_unknown_channel_is_not_found_rather_than_falling_back(session, project) -> None:
    """The design rule: an exact tool refuses rather than guessing. Returning the
    newest thread from some other channel would be a plausible wrong answer."""
    await _ingest_threads(session, [_thread("eng", "1000100000.000100", headline="Only thread")])
    await _mark_synced(session, "slack")

    lookup = await latest_slack_threads(session, "test-project", channel="nonexistent")

    assert lookup.status == "not_found"
    assert lookup.threads == []
    assert lookup.available == 0


async def test_slack_lookup_is_scoped_to_the_project(session, project, other_project) -> None:
    await ingest_documents(
        session,
        slack_thread_documents(
            "other-project", [_thread("eng", "1000900000.000100", headline="Other project thread")]
        ),
        None,
    )
    await _ingest_threads(session, [_thread("eng", "1000100000.000100", headline="Ours")])
    await _mark_synced(session, "slack")

    lookup = await latest_slack_threads(session, "test-project")

    assert lookup.available == 1
    assert lookup.threads[0].headline == "Ours"


async def test_slack_freshness_marks_a_stale_index(session, project) -> None:
    """Staleness is why an otherwise correct answer grades down, so it has to be observable."""
    await _ingest_threads(session, [_thread("eng", "1000100000.000100", headline="Thread")])
    await _mark_synced(session, "slack", minutes_ago=settings.slack_sync_stale_after_minutes + 5)

    lookup = await latest_slack_threads(session, "test-project")

    assert lookup.status == "found"
    assert lookup.stale is True


async def test_no_author_returns_the_newest_commit_in_the_project(session, project) -> None:
    """"What was the last commit?" names nobody, and the answer is still well defined."""
    await _ingest_commits(
        session,
        [
            _commit("old", "Raghav Rao", login="raghav-dev", at="2026-08-01T09:00:00Z"),
            _commit("newest", "Someone Else", login="someone", at="2026-08-20T09:00:00Z"),
            _commit("middle", "Raghav Rao", login="raghav-dev", at="2026-08-10T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project")

    assert lookup.status == "found"
    # Crosses author boundaries: the newest commit overall belongs to a different person than the
    # one with the most commits, which is exactly the case an author filter would get wrong.
    assert lookup.sha == "newest"
    assert lookup.author == "Someone Else"


async def test_no_author_still_counts_offsets_across_the_whole_project(session, project) -> None:
    await _ingest_commits(
        session,
        [
            _commit("third", "A", login="a", at="2026-08-01T09:00:00Z"),
            _commit("first", "B", login="b", at="2026-08-20T09:00:00Z"),
            _commit("second", "C", login="c", at="2026-08-10T09:00:00Z"),
        ],
    )

    assert (await latest_commit_by_author(session, "test-project", None, 1)).sha == "second"
    assert (await latest_commit_by_author(session, "test-project", None, 2)).sha == "third"


async def test_no_author_beyond_the_history_is_out_of_range_not_a_wrong_commit(
    session, project
) -> None:
    await _ingest_commits(
        session, [_commit("only", "A", login="a", at="2026-08-01T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(session, "test-project", None, 4)

    assert lookup.status == "out_of_range"
    assert lookup.available == 1


async def test_no_author_on_an_empty_project_is_not_found(session, project) -> None:
    lookup = await latest_commit_by_author(session, "test-project")

    assert lookup.status == "not_found"
    assert lookup.record is None


# --- GW-9: identity resolution under more than one contributor ---------------------------------


async def test_one_person_under_two_display_names_resolves(session, project) -> None:
    """The real case in both indexed repos: GitHub reports one human two ways.

    `Manav0411` on some commits and `Manav Goel` on others, same login and same email. An exact
    query hits the identity array and unifies them, which is why this has never surfaced. A partial
    query goes down a different path that compares display names, sees two, and refuses.
    """
    await _ingest_commits(
        session,
        [
            _commit("a", "Manav0411", login="manav0411", at="2026-08-01T09:00:00Z"),
            _commit("b", "Manav Goel", login="manav0411", at="2026-08-05T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "manav")

    assert lookup.status == "found", f"one human reported as {lookup.status}: {lookup.candidates}"
    assert lookup.sha == "b"


async def test_an_internal_double_space_in_a_name_still_matches(session, project) -> None:
    """Ingestion and lookup must fold whitespace the same way.

    Ingestion stored `strip().casefold()` while lookup searched for
    `" ".join(casefold().split())`, so a name carrying a double space was written one way and
    searched another and could never be found.
    """
    await _ingest_commits(
        session, [_commit("a", "Raghav  Rao", login="raghav-rao", at="2026-08-01T09:00:00Z")]
    )

    lookup = await latest_commit_by_author(session, "test-project", "Raghav Rao")

    assert lookup.status == "found"
    assert lookup.sha == "a"


async def test_a_partial_query_naming_one_of_several_contributors_resolves(
    session, project
) -> None:
    """Several distinct people indexed, but the query identifies exactly one of them."""
    await _ingest_commits(
        session,
        [
            _commit("a", "Raghav Rao", login="raghav-rao", at="2026-08-01T09:00:00Z"),
            _commit("b", "Priya Nair", login="priya-nair", at="2026-08-02T09:00:00Z"),
            _commit("c", "Sam Okafor", login="sam-okafor", at="2026-08-03T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "priya")

    assert lookup.status == "found"
    assert lookup.sha == "b"
    assert lookup.author == "Priya Nair"


async def test_three_distinct_contributors_sharing_a_prefix_are_all_reported(
    session, project
) -> None:
    """Ambiguity must list everyone who matched, not just the first two."""
    await _ingest_commits(
        session,
        [
            _commit("a", "Raghav Rao", login="raghav-rao", at="2026-08-01T09:00:00Z"),
            _commit("b", "Raghav Menon", login="raghav-menon", at="2026-08-02T09:00:00Z"),
            _commit("c", "Raghav Iyer", login="raghav-iyer", at="2026-08-03T09:00:00Z"),
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "raghav")

    assert lookup.status == "ambiguous"
    assert lookup.record is None
    assert lookup.candidates == ["Raghav Iyer", "Raghav Menon", "Raghav Rao"]


async def test_offsets_still_count_within_one_person_when_others_are_indexed(
    session, project
) -> None:
    """The offset path has only ever run on a single-author corpus."""
    await _ingest_commits(
        session,
        [
            _commit("p1", "Priya Nair", login="priya-nair", at="2026-08-01T09:00:00Z"),
            _commit("other", "Sam Okafor", login="sam-okafor", at="2026-08-02T09:00:00Z"),
            _commit("p2", "Priya Nair", login="priya-nair", at="2026-08-03T09:00:00Z"),
        ],
    )

    # Newest-first for Priya is p2 then p1; Sam's commit sits between them by date and must not
    # be counted.
    assert (await latest_commit_by_author(session, "test-project", "priya")).sha == "p2"
    assert (await latest_commit_by_author(session, "test-project", "priya", 1)).sha == "p1"


async def test_jira_one_person_under_two_display_names_resolves(session, project) -> None:
    """The GitHub fix applied to the Jira path, so the two cannot drift apart."""
    await _ingest_issues(
        session,
        [
            _issue(
                "TEST-1",
                assignee="Manav Goel",
                account_id="acct-1",
                updated="2026-08-01T09:00:00Z",
            ),
            _issue(
                "TEST-2",
                assignee="Manav0411",
                account_id="acct-1",
                updated="2026-08-15T09:00:00Z",
            ),
        ],
    )

    lookup = await jira_issues_by_assignee(session, "test-project", "manav")

    assert lookup.status == "found", f"one human reported as {lookup.status}: {lookup.candidates}"
    assert [issue.key for issue in lookup.issues] == ["TEST-2", "TEST-1"]


async def test_ambiguity_sees_contributors_outside_the_recent_window(session, project) -> None:
    """Found on `pallets/flask`, and only findable on a real multi-contributor corpus.

    Ambiguity used to be decided over the 100 newest commits, filtered in Python. On that repo
    only 9 of 51 contributors fell inside that window, so a query matching two people resolved
    confidently to whichever one was recent: "davi" matched David Lord (396 commits, newest) and
    David (1 commit, 198th) and answered as David Lord with no disclosure.

    The window here is deliberately larger than 100 so the older contributor is outside it.
    """
    commits = [
        _commit("old-davi", "Davina Cole", login="davina", at="2024-01-01T09:00:00Z"),
        *[
            _commit(f"recent-{index}", "David Lord", login="davidism", at="2026-08-01T09:00:00Z")
            for index in range(120)
        ],
    ]
    await _ingest_commits(session, commits)

    lookup = await latest_commit_by_author(session, "test-project", "davi")

    assert lookup.status == "ambiguous", f"answered as {lookup.author!r}, hiding the other match"
    assert lookup.candidates == ["David Lord", "Davina Cole"]


async def test_a_token_search_cannot_span_two_identities(session, project) -> None:
    """Identities are matched individually, never as one concatenated string."""
    await _ingest_commits(
        session, [_commit("a", "Raghav Rao", login="rao-r", at="2026-08-01T09:00:00Z")]
    )

    # "rao" ends one identity and "rao-r" begins another; a joined-string search would match.
    assert (await latest_commit_by_author(session, "test-project", "rao rao-r")).status != "found"


async def test_a_position_past_the_lookup_window_is_refused_not_clamped(session, project) -> None:
    """Found on `pallets/flask`: "the 105th commit by davidism" returned the 100th.

    The offset was clamped to the window before the range check ran, so every position past the
    end resolved to the last commit that did exist. The extractor's own comment already said an
    out-of-range position is answered by refusing rather than by clamping; the code did not.
    """
    await _ingest_commits(
        session,
        [
            _commit(
                f"c{index:03d}",
                "David Lord",
                login="davidism",
                at=f"2026-08-01T09:{index:02d}:00Z",
            )
            for index in range(60)
        ],
    )

    lookup = await latest_commit_by_author(session, "test-project", "David Lord", 104)

    assert lookup.status == "out_of_range"
    assert lookup.record is None and lookup.sha is None
    assert lookup.offset == 104

