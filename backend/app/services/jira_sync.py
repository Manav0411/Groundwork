from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.jira import JiraConnector, JiraRateLimitError
from app.core.config import settings
from app.db.models import ConnectorSyncState, Project
from app.services.ingestion import ingest_documents, jira_issue_documents
from app.services.llm import OllamaClient


@dataclass(frozen=True)
class JiraSyncReport:
    project_id: str
    jira_project_key: str
    fetched: int
    pages_fetched: int
    documents: int
    chunks: int
    embedded: int
    incremental_since: datetime | None
    rate_limit_remaining: int | None
    completed_at: datetime


class JiraSyncInProgressError(RuntimeError):
    """Raised when a recent Jira sync is already running for the project."""


async def _mark_started(
    session: AsyncSession, project_id: str, started_at: datetime
) -> ConnectorSyncState:
    statement = insert(ConnectorSyncState).values(
        project_id=project_id,
        source_type="jira",
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
        raise RuntimeError("Could not initialize Jira sync state.")
    # The upsert bypasses the ORM identity map. Refresh so a previous in-memory
    # `succeeded` value does not make the final status assignment look unchanged.
    await session.refresh(state)
    return state


async def sync_jira_project(
    session: AsyncSession,
    project_id: str,
    *,
    connector: JiraConnector | None = None,
    max_issues: int | None = None,
) -> JiraSyncReport:
    project = await session.get(Project, project_id)
    if project is None:
        raise LookupError(f"Project {project_id!r} does not exist.")
    jira_project_key = project.jira_project_key or settings.jira_project_key
    if not jira_project_key:
        raise ValueError(
            f"Project {project_id!r} has no Jira project key. Configure the Jira connector first."
        )

    existing_state = await session.scalar(
        select(ConnectorSyncState).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "jira",
        )
    )
    started_at = datetime.now(UTC)
    running_timeout = timedelta(minutes=settings.jira_sync_running_timeout_minutes)
    if (
        existing_state is not None
        and existing_state.status == "running"
        and existing_state.last_started_at is not None
        and started_at - existing_state.last_started_at < running_timeout
    ):
        raise JiraSyncInProgressError(f"A Jira sync for {project_id!r} is already running.")
    previous_success = existing_state.last_succeeded_at if existing_state else None
    overlap = timedelta(minutes=settings.jira_sync_overlap_minutes)
    incremental_since = previous_success - overlap if previous_success else None
    state = await _mark_started(session, project_id, started_at)
    # Held as a plain value: see the note in `github_sync.py`. Reading `state.id` after the
    # failure path's rollback raises MissingGreenlet and masks the real connector error.
    state_id = state.id

    try:
        result = await (connector or JiraConnector()).list_issues(
            jira_project_key,
            updated_since=incremental_since,
            max_issues=max(1, min(max_issues or settings.jira_sync_max_issues, 2_000)),
        )
        stats = await ingest_documents(
            session,
            jira_issue_documents(project_id, result.issues),
            OllamaClient(),
        )
        completed_at = datetime.now(UTC)
        project.jira_project_key = jira_project_key
        state.status = "succeeded"
        state.last_succeeded_at = completed_at
        state.last_error = None
        state.rate_limit_remaining = result.rate_limit_remaining
        state.rate_limit_reset_at = None
        await session.commit()
        return JiraSyncReport(
            project_id=project_id,
            jira_project_key=jira_project_key,
            fetched=len(result.issues),
            pages_fetched=result.pages_fetched,
            incremental_since=incremental_since,
            rate_limit_remaining=result.rate_limit_remaining,
            completed_at=completed_at,
            **stats,
        )
    except Exception as exc:
        await session.rollback()
        state = await session.get(ConnectorSyncState, state_id)
        if state is not None:
            state.status = "failed"
            state.last_error = str(exc)[:2_000]
            if isinstance(exc, JiraRateLimitError):
                state.rate_limit_remaining = 0
                state.rate_limit_reset_at = (
                    datetime.now(UTC) + timedelta(seconds=exc.retry_after_seconds)
                    if exc.retry_after_seconds is not None
                    else None
                )
            await session.commit()
        raise
