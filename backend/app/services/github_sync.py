from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.github import GitHubConnector, GitHubRateLimitError
from app.core.config import settings
from app.db.models import ConnectorSyncState, Project
from app.services.ingestion import github_commit_documents, ingest_documents
from app.services.llm import OllamaClient


@dataclass(frozen=True)
class GitHubSyncReport:
    project_id: str
    repo: str
    fetched: int
    pages_fetched: int
    documents: int
    chunks: int
    embedded: int
    incremental_since: datetime | None
    rate_limit_remaining: int | None
    rate_limit_reset_at: datetime | None
    completed_at: datetime


class GitHubSyncInProgressError(RuntimeError):
    """Raised when a recent GitHub sync is already running for the project."""


async def _mark_started(
    session: AsyncSession, project_id: str, started_at: datetime
) -> ConnectorSyncState:
    statement = insert(ConnectorSyncState).values(
        project_id=project_id,
        source_type="github",
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
        raise RuntimeError("Could not initialize GitHub sync state.")
    # The upsert bypasses the ORM identity map. Refresh so a previous in-memory
    # `succeeded` value does not make the final status assignment look unchanged.
    await session.refresh(state)
    return state


async def sync_github_project(
    session: AsyncSession,
    project_id: str,
    *,
    connector: GitHubConnector | None = None,
    max_commits: int | None = None,
) -> GitHubSyncReport:
    project = await session.get(Project, project_id)
    if project is None:
        raise LookupError(f"Project {project_id!r} does not exist.")

    existing_state = await session.scalar(
        select(ConnectorSyncState).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "github",
        )
    )
    started_at = datetime.now(UTC)
    running_timeout = timedelta(minutes=settings.github_sync_running_timeout_minutes)
    if (
        existing_state is not None
        and existing_state.status == "running"
        and existing_state.last_started_at is not None
        and started_at - existing_state.last_started_at < running_timeout
    ):
        raise GitHubSyncInProgressError(f"A GitHub sync for {project_id!r} is already running.")
    previous_success = existing_state.last_succeeded_at if existing_state else None
    overlap = timedelta(minutes=settings.github_sync_overlap_minutes)
    incremental_since = previous_success - overlap if previous_success else None
    state = await _mark_started(session, project_id, started_at)

    try:
        result = await (connector or GitHubConnector()).list_commits(
            project.repo,
            since=incremental_since,
            max_commits=max(1, min(max_commits or settings.github_sync_max_commits, 1000)),
        )
        stats = await ingest_documents(
            session,
            github_commit_documents(project_id, result.commits),
            OllamaClient(),
        )
        completed_at = datetime.now(UTC)
        state.status = "succeeded"
        state.last_succeeded_at = completed_at
        state.last_error = None
        state.rate_limit_remaining = result.rate_limit_remaining
        state.rate_limit_reset_at = result.rate_limit_reset_at
        await session.commit()
        return GitHubSyncReport(
            project_id=project_id,
            repo=project.repo,
            fetched=len(result.commits),
            pages_fetched=result.pages_fetched,
            incremental_since=incremental_since,
            rate_limit_remaining=result.rate_limit_remaining,
            rate_limit_reset_at=result.rate_limit_reset_at,
            completed_at=completed_at,
            **stats,
        )
    except Exception as exc:
        await session.rollback()
        state = await session.get(ConnectorSyncState, state.id)
        if state is not None:
            state.status = "failed"
            state.last_error = str(exc)[:2000]
            if isinstance(exc, GitHubRateLimitError):
                state.rate_limit_remaining = 0
                state.rate_limit_reset_at = exc.reset_at
            await session.commit()
        raise
