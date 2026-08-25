"""The connector sync state machine against a real database.

This file exists mainly for one line. `_mark_started` upserts through SQLAlchemy Core, which
bypasses the ORM identity map, so a stale in-memory `succeeded` made the later
`state.status = "succeeded"` assignment look like a no-op and left the row stuck on `running`.
That bug shipped, was found by hand, and is trivially reintroduced by anyone copying the shape of
the function without its comment. `test_terminal_status_is_actually_persisted` is the guard.

Beyond that: the overlap cursor, the running-lock, and the failure path that must never advance
the success cursor.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.connectors.github import GitHubCommit, GitHubCommitPageResult, GitHubRateLimitError
from app.connectors.slack import SlackMessage, SlackRateLimitError, SlackThread
from app.core.config import settings
from app.db.models import ConnectorSyncState
from app.services.github_sync import GitHubSyncInProgressError, sync_github_project
from app.services.slack_sync import SlackSyncInProgressError, sync_slack_project

pytestmark = pytest.mark.integration


class FakeGitHubConnector:
    """Records what the sync asked for, which is where the cursor contract is observable."""

    def __init__(
        self, commits=None, *, error: Exception | None = None, remaining: int | None = 4_999
    ):
        self.commits = commits if commits is not None else [_commit("sha-1")]
        self.error = error
        self.remaining = remaining
        self.since_values: list[datetime | None] = []

    async def list_commits(self, repo, *, since=None, max_commits=500):
        self.since_values.append(since)
        if self.error is not None:
            raise self.error
        return GitHubCommitPageResult(
            commits=self.commits,
            pages_fetched=1,
            rate_limit_remaining=self.remaining,
            rate_limit_reset_at=None,
        )


class FakeSlackConnector:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.oldest_values: list[datetime | None] = []

    async def list_threads(self, channel_id, *, channel_name=None, oldest=None, max_messages=500):
        from app.connectors.slack import SlackThreadPageResult

        self.oldest_values.append(oldest)
        if self.error is not None:
            raise self.error
        thread = SlackThread(
            channel_id=channel_id,
            channel_name="engineering",
            thread_ts="1700000000.000100",
            messages=[
                SlackMessage(ts="1700000000.000100", user_id="U1", author="Raghav", text="Ship it.")
            ],
            permalink="https://groundwork.slack.com/archives/C1/p1700000000000100",
        )
        return SlackThreadPageResult(threads=[thread], pages_fetched=1, rate_limit_remaining=None)


def _commit(sha: str) -> GitHubCommit:
    return GitHubCommit(
        sha=sha,
        message=f"Commit {sha}",
        author="Raghav Rao",
        author_email="raghav@example.com",
        author_login="raghav-dev",
        committer="Raghav Rao",
        authored_at="2026-08-01T09:00:00Z",
        committed_at="2026-08-01T09:00:00Z",
        url=f"https://github.com/acme/test/commit/{sha}",
    )


async def _state(session, source_type: str) -> ConnectorSyncState | None:
    return await session.scalar(
        select(ConnectorSyncState).where(ConnectorSyncState.source_type == source_type)
    )


async def test_first_sync_has_no_cursor_and_records_success(session, project) -> None:
    connector = FakeGitHubConnector()

    report = await sync_github_project(session, "test-project", connector=connector)

    assert connector.since_values == [None], "A first sync must not invent a cursor."
    assert report.incremental_since is None
    assert report.fetched == 1
    state = await _state(session, "github")
    assert state.status == "succeeded"
    assert state.last_succeeded_at is not None
    assert state.rate_limit_remaining == 4_999


async def test_terminal_status_is_actually_persisted(session, project) -> None:
    """Regression test for the identity-map bug.

    Removing `await session.refresh(state)` from `_mark_started` must make this fail. The row is
    re-read through a fresh query rather than the ORM instance the sync holds, because reading the
    instance is precisely what hid the bug.
    """
    await sync_github_project(session, "test-project", connector=FakeGitHubConnector())
    session.expunge_all()

    first = await _state(session, "github")
    assert first.status == "succeeded"
    # Read out as a plain value. Holding the instance would be useless: the second sync loads the
    # same row into the identity map and mutates it, so `first.last_succeeded_at` would silently
    # become the second sync's timestamp — the same aliasing hazard as the bug this file guards.
    first_succeeded_at = first.last_succeeded_at

    # A second sync passes through `running` again; the terminal status must still land.
    await sync_github_project(session, "test-project", connector=FakeGitHubConnector())
    session.expunge_all()

    second = await _state(session, "github")
    assert second.status == "succeeded"
    assert second.last_succeeded_at > first_succeeded_at


async def test_second_sync_uses_an_overlap_cursor(session, project) -> None:
    """The overlap window is what stops a commit landing between syncs from being missed."""
    connector = FakeGitHubConnector()
    await sync_github_project(session, "test-project", connector=connector)
    succeeded_at = (await _state(session, "github")).last_succeeded_at

    await sync_github_project(session, "test-project", connector=connector)

    assert connector.since_values[0] is None
    cursor = connector.since_values[1]
    overlap = timedelta(minutes=settings.github_sync_overlap_minutes)
    assert cursor == succeeded_at - overlap
    assert cursor < succeeded_at, "The cursor must look backwards, never forwards."


async def test_failed_sync_records_the_error_and_keeps_the_success_cursor(session, project) -> None:
    """A failure must never advance `last_succeeded_at`, or the gap is silently skipped forever."""
    await sync_github_project(session, "test-project", connector=FakeGitHubConnector())
    succeeded_at = (await _state(session, "github")).last_succeeded_at

    with pytest.raises(RuntimeError, match="upstream exploded"):
        await sync_github_project(
            session,
            "test-project",
            connector=FakeGitHubConnector(error=RuntimeError("upstream exploded")),
        )

    session.expunge_all()
    state = await _state(session, "github")
    assert state.status == "failed"
    assert "upstream exploded" in state.last_error
    assert state.last_succeeded_at == succeeded_at


async def test_error_messages_are_bounded(session, project) -> None:
    """An unbounded provider error would otherwise be written to the row verbatim."""
    with pytest.raises(RuntimeError):
        await sync_github_project(
            session,
            "test-project",
            connector=FakeGitHubConnector(error=RuntimeError("x" * 5_000)),
        )

    session.expunge_all()
    assert len((await _state(session, "github")).last_error) == 2_000


async def test_rate_limit_error_records_the_reset_time(session, project) -> None:
    reset_at = datetime.now(UTC) + timedelta(minutes=30)

    with pytest.raises(GitHubRateLimitError):
        await sync_github_project(
            session,
            "test-project",
            connector=FakeGitHubConnector(error=GitHubRateLimitError(reset_at=reset_at)),
        )

    session.expunge_all()
    state = await _state(session, "github")
    assert state.rate_limit_remaining == 0
    assert state.rate_limit_reset_at == reset_at


async def test_a_recent_running_sync_blocks_a_duplicate(session, project) -> None:
    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type="github",
            status="running",
            last_started_at=datetime.now(UTC),
        )
    )
    await session.flush()

    with pytest.raises(GitHubSyncInProgressError):
        await sync_github_project(session, "test-project", connector=FakeGitHubConnector())


async def test_a_stale_running_sync_does_not_block_forever(session, project) -> None:
    """A crashed sync leaves `running` behind; the timeout is what makes the system recoverable."""
    timeout = settings.github_sync_running_timeout_minutes
    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type="github",
            status="running",
            last_started_at=datetime.now(UTC) - timedelta(minutes=timeout + 1),
        )
    )
    await session.flush()

    report = await sync_github_project(session, "test-project", connector=FakeGitHubConnector())

    assert report.fetched == 1
    session.expunge_all()
    assert (await _state(session, "github")).status == "succeeded"


async def test_unknown_project_is_rejected_before_any_state_is_written(session) -> None:
    with pytest.raises(LookupError):
        await sync_github_project(session, "no-such-project", connector=FakeGitHubConnector())

    assert await _state(session, "github") is None


async def test_slack_sync_follows_the_same_contract(session, project) -> None:
    """The connector contract is meant to be reusable; a third source should need no new rules."""
    connector = FakeSlackConnector()

    report = await sync_slack_project(session, "test-project", connector=connector)

    assert connector.oldest_values == [None]
    assert report.channels == ["C1"]
    assert report.fetched == 1
    session.expunge_all()
    assert (await _state(session, "slack")).status == "succeeded"

    await sync_slack_project(session, "test-project", connector=connector)
    assert connector.oldest_values[1] is not None


async def test_slack_sync_without_configured_channels_is_rejected(session, other_project) -> None:
    """Indexing scope is a deliberate choice, so an unconfigured project must not sync anything."""
    with pytest.raises(ValueError, match="no Slack channels"):
        await sync_slack_project(session, "other-project", connector=FakeSlackConnector())


async def test_slack_rate_limit_sets_a_reset_window(session, project) -> None:
    with pytest.raises(SlackRateLimitError):
        await sync_slack_project(
            session,
            "test-project",
            connector=FakeSlackConnector(error=SlackRateLimitError(retry_after_seconds=30)),
        )

    session.expunge_all()
    state = await _state(session, "slack")
    assert state.status == "failed"
    assert state.rate_limit_remaining == 0
    assert state.rate_limit_reset_at is not None


async def test_slack_running_lock_is_independent_of_github(session, project) -> None:
    """State is per project *and* source, so one stuck connector must not block another."""
    session.add(
        ConnectorSyncState(
            project_id="test-project",
            source_type="slack",
            status="running",
            last_started_at=datetime.now(UTC),
        )
    )
    await session.flush()

    with pytest.raises(SlackSyncInProgressError):
        await sync_slack_project(session, "test-project", connector=FakeSlackConnector())

    # GitHub is unaffected.
    report = await sync_github_project(session, "test-project", connector=FakeGitHubConnector())
    assert report.fetched == 1
