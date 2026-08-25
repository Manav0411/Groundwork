"""Slack synchronization.

Mirrors `jira_sync.py`. The one detail that must not be simplified is the ORM refresh inside
`_mark_started`: the upsert bypasses the identity map, and without the refresh a stale in-memory
`succeeded` makes the final status assignment look unchanged to SQLAlchemy, leaving the row stuck on
`running`. That failure is recorded in the build journey and is easy to reintroduce by copying the
shape of this function without the comment.

Unlike GitHub and Jira, a Slack sync spans several channels, so the report aggregates across them
and one unreachable channel fails the whole run rather than silently indexing a subset.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.slack import SlackConnector, SlackRateLimitError, SlackThread
from app.core.config import settings
from app.db.models import ConnectorSyncState, Project
from app.services.ingestion import ingest_documents, slack_thread_documents
from app.services.llm import OllamaClient


@dataclass(frozen=True)
class SlackSyncReport:
    project_id: str
    channels: list[str]
    fetched: int
    pages_fetched: int
    documents: int
    chunks: int
    embedded: int
    incremental_since: datetime | None
    rate_limit_remaining: int | None
    completed_at: datetime


class SlackSyncInProgressError(RuntimeError):
    """Raised when a recent Slack sync is already running for the project."""


async def _mark_started(
    session: AsyncSession, project_id: str, started_at: datetime
) -> ConnectorSyncState:
    statement = insert(ConnectorSyncState).values(
        project_id=project_id,
        source_type="slack",
        status="running",
        last_started_at=started_at,
        last_error=None,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_connector_sync_states_project_id",
        set_={
            "status": "running",
            "last_started_at": started_at,
            "last_error": None,
            "updated_at": func.now(),
        },
    ).returning(ConnectorSyncState.id)
    state_id = (await session.execute(statement)).scalar_one()
    await session.commit()
    state = await session.get(ConnectorSyncState, state_id)
    if state is None:
        raise RuntimeError("Could not initialize Slack sync state.")
    # The upsert bypasses the ORM identity map. Refresh so a previous in-memory `succeeded` value
    # does not make the final status assignment look unchanged.
    await session.refresh(state)
    return state


async def sync_slack_project(
    session: AsyncSession,
    project_id: str,
    *,
    connector: SlackConnector | None = None,
    max_messages: int | None = None,
) -> SlackSyncReport:
    project = await session.get(Project, project_id)
    if project is None:
        raise LookupError(f"Project {project_id!r} does not exist.")
    channels = list(project.slack_channel_ids or [])
    if not channels:
        raise ValueError(
            f"Project {project_id!r} has no Slack channels. Configure the Slack connector first."
        )

    existing_state = await session.scalar(
        select(ConnectorSyncState).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "slack",
        )
    )
    started_at = datetime.now(UTC)
    running_timeout = timedelta(minutes=settings.slack_sync_running_timeout_minutes)
    if (
        existing_state is not None
        and existing_state.status == "running"
        and existing_state.last_started_at is not None
        and started_at - existing_state.last_started_at < running_timeout
    ):
        raise SlackSyncInProgressError(f"A Slack sync for {project_id!r} is already running.")
    previous_success = existing_state.last_succeeded_at if existing_state else None
    overlap = timedelta(minutes=settings.slack_sync_overlap_minutes)
    incremental_since = previous_success - overlap if previous_success else None
    state = await _mark_started(session, project_id, started_at)
    # Held as a plain value: see the note in `github_sync.py`. Reading `state.id` after the
    # failure path's rollback raises MissingGreenlet and masks the real connector error.
    state_id = state.id

    try:
        client = connector or SlackConnector()
        budget = max(1, min(max_messages or settings.slack_sync_max_messages, 5_000))
        threads: list[SlackThread] = []
        pages_fetched = 0
        remaining: int | None = None
        for channel_id in channels:
            result = await client.list_threads(
                channel_id,
                oldest=incremental_since,
                max_messages=max(1, budget // len(channels)),
            )
            threads.extend(result.threads)
            pages_fetched += result.pages_fetched
            if result.rate_limit_remaining is not None:
                remaining = (
                    result.rate_limit_remaining
                    if remaining is None
                    else min(remaining, result.rate_limit_remaining)
                )

        stats = await ingest_documents(
            session, slack_thread_documents(project_id, threads), OllamaClient()
        )
        completed_at = datetime.now(UTC)
        state.status = "succeeded"
        state.last_succeeded_at = completed_at
        state.last_error = None
        state.rate_limit_remaining = remaining
        state.rate_limit_reset_at = None
        await session.commit()
        return SlackSyncReport(
            project_id=project_id,
            channels=channels,
            fetched=len(threads),
            pages_fetched=pages_fetched,
            incremental_since=incremental_since,
            rate_limit_remaining=remaining,
            completed_at=completed_at,
            **stats,
        )
    except Exception as exc:
        await session.rollback()
        state = await session.get(ConnectorSyncState, state_id)
        if state is not None:
            state.status = "failed"
            state.last_error = str(exc)[:2_000]
            if isinstance(exc, SlackRateLimitError):
                state.rate_limit_remaining = 0
                state.rate_limit_reset_at = (
                    datetime.now(UTC) + timedelta(seconds=exc.retry_after_seconds)
                    if exc.retry_after_seconds is not None
                    else None
                )
            await session.commit()
        raise
