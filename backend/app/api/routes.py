import asyncio
from dataclasses import asdict
from typing import Annotated

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import run_agent
from app.connectors.github import GitHubRateLimitError
from app.connectors.jira import JiraRateLimitError
from app.connectors.slack import SlackAPIError, SlackRateLimitError
from app.core.observability import SYNCS, logger, render_metrics
from app.core.security import require_api_key
from app.db.models import ConnectorSyncState, Project, SourceDocument
from app.db.session import SessionFactory, get_optional_session, get_session
from app.models.schemas import (
    JiraProjectConfig,
    ProjectCreate,
    ProjectSummary,
    QueryRequest,
    QueryResponse,
    SlackProjectConfig,
    TimelineItem,
)
from app.services.github_sync import GitHubSyncInProgressError, sync_github_project
from app.services.ingestion import seed_synthetic_workspace
from app.services.jira_sync import JiraSyncInProgressError, sync_jira_project
from app.services.llm import chat_client, embedding_client
from app.services.persistence import (
    ConversationNotFoundError,
    load_conversation_history,
    load_trace,
)
from app.services.slack_sync import SlackSyncInProgressError, sync_slack_project

SYNC_RUNNERS = {
    "github": sync_github_project,
    "jira": sync_jira_project,
    "slack": sync_slack_project,
}

router = APIRouter()
DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
OptionalDatabaseSession = Annotated[AsyncSession | None, Depends(get_optional_session)]


def _project_summary(project: Project) -> ProjectSummary:
    """One place that decides what a project looks like over the wire."""
    return ProjectSummary(
        id=project.id,
        name=project.name,
        repo=project.repo,
        jira_project_key=project.jira_project_key,
        slack_channel_ids=list(project.slack_channel_ids or []),
        status=project.status,
        health=project.health,
    )


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "groundwork"}


@router.get("/health/ollama")
async def ollama_health() -> dict[str, object]:
    """Report the chat and embedding providers separately, because they can now differ.

    In a deployed configuration chat runs on a hosted API while embeddings stay local, so a single
    status would hide exactly the failure this arrangement introduces: a healthy chat provider and
    a dead embedder still means retrieval returns nothing. The path keeps its name because the
    frontend proxy allowlist matches it.
    """
    chat, embeddings = await asyncio.gather(
        chat_client("synthesis").health(), embedding_client().health()
    )
    return {
        **chat.__dict__,
        "role": "chat",
        "embeddings": {**embeddings.__dict__, "role": "embeddings"},
    }


@router.get("/metrics", dependencies=[Depends(require_api_key)])
async def metrics() -> Response:
    """Prometheus exposition.

    Behind the API key like everything else. Usage volume and error rates are not secrets exactly,
    but the endpoint is public and there is no reason to hand a stranger a map of how the demo is
    doing.
    """
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


@router.get("/health/database")
async def database_health(
    session: OptionalDatabaseSession,
) -> dict[str, object]:
    migrated = False
    if session is not None:
        migrated = bool(
            await session.scalar(text("select to_regclass('public.projects') is not null"))
        )
    return {
        "provider": "postgresql",
        "available": session is not None,
        "migration_required": session is not None and not migrated,
    }


@router.post("/query", response_model=QueryResponse, dependencies=[Depends(require_api_key)])
async def query(
    request: QueryRequest,
    session: OptionalDatabaseSession,
) -> QueryResponse:
    try:
        return await run_agent(request, session)
    except ConversationNotFoundError as exc:
        # An unknown or cross-project conversation id is an error, not a quiet new conversation:
        # silently starting over would look like the agent had simply forgotten the thread.
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _run_sync_detached(source: str, project_id: str, limit: int | None) -> None:
    """Run one sync on its own session, after the response has gone.

    A fresh session is not optional. The request-scoped one is closed as soon as the response is
    sent, so reusing it here fails somewhere inside the connector with an error about a closed
    connection rather than anything that names the real cause.

    Nothing is raised. The caller is gone; the outcome is already durable in
    `connector_sync_states`, which is what the status endpoint reads, and it is logged and counted
    here so a failure is visible without querying the database.
    """
    kwargs = {"max_commits": limit} if source == "github" else (
        {"max_issues": limit} if source == "jira" else {"max_messages": limit}
    )
    try:
        async with SessionFactory() as session:
            report = await SYNC_RUNNERS[source](session, project_id, **kwargs)
        SYNCS.labels(source=source, outcome="succeeded").inc()
        logger.info(
            "background sync finished",
            extra={
                "source": source,
                "project": project_id,
                "documents": getattr(report, "documents", None),
                "embedded": getattr(report, "embedded", None),
            },
        )
    except Exception as exc:
        SYNCS.labels(source=source, outcome="failed").inc()
        logger.exception(
            "background sync failed",
            extra={"source": source, "project": project_id, "error": str(exc)[:200]},
        )


async def _start_background_sync(
    background_tasks: BackgroundTasks,
    session: AsyncSession,
    source: str,
    project_id: str,
    limit: int | None,
) -> dict[str, object]:
    """Accept a sync for later, rejecting up front what can be rejected up front.

    An unknown project and an already-running sync are both answered synchronously, because a 202
    for work that was never going to happen is worse than a slow response: the caller polls a status
    that never changes and has nothing to explain why.
    """
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} does not exist.")

    state = (
        await session.execute(
            select(ConnectorSyncState).where(
                ConnectorSyncState.project_id == project_id,
                ConnectorSyncState.source_type == source,
            )
        )
    ).scalar_one_or_none()
    if state is not None and state.status == "running":
        raise HTTPException(
            status_code=409, detail=f"A {source} sync for {project_id!r} is already running."
        )

    background_tasks.add_task(_run_sync_detached, source, project_id, limit)
    return {
        "status": "accepted",
        "project_id": project_id,
        "source": source,
        "poll": f"/projects/{project_id}/sync/{source}",
    }


@router.post("/ingest/workspace", dependencies=[Depends(require_api_key)])
async def ingest_workspace(session: DatabaseSession) -> dict[str, object]:
    stats = await seed_synthetic_workspace(session)
    return {"status": "loaded", **stats}


@router.post("/projects/{project_id}/sync/github", dependencies=[Depends(require_api_key)])
async def sync_project_github(
    project_id: str,
    session: DatabaseSession,
    background_tasks: BackgroundTasks,
    max_commits: int | None = None,
    background: bool = False,
) -> dict[str, object]:
    """Sync GitHub. `background=true` returns immediately and the caller polls the status endpoint.

    Synchronous by default, deliberately. The eval harness posts a sync and queries straight
    afterwards, so flipping the default would leave the release gates passing against stale data --
    which is worse than failing, because nothing would say so.
    """
    if background:
        return await _start_background_sync(
            background_tasks, session, "github", project_id, max_commits
        )
    return await _sync_github_project(session, project_id, max_commits)


async def _sync_github_project(
    session: AsyncSession, project_id: str, max_commits: int | None
) -> dict[str, object]:
    try:
        report = await sync_github_project(session, project_id, max_commits=max_commits)
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except GitHubRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "message": str(exc),
                "reset_at": exc.reset_at.isoformat() if exc.reset_at else None,
            },
        ) from exc
    except GitHubSyncInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API request failed: {exc}") from exc
    return {"status": "synced", **asdict(report)}


@router.put(
    "/projects/{project_id}/connectors/jira",
    response_model=ProjectSummary,
    dependencies=[Depends(require_api_key)],
)
async def configure_project_jira(
    project_id: str,
    request: JiraProjectConfig,
    session: DatabaseSession,
) -> ProjectSummary:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} does not exist.")
    project.jira_project_key = request.project_key
    await session.commit()
    return _project_summary(project)


@router.post("/projects/{project_id}/sync/jira", dependencies=[Depends(require_api_key)])
async def sync_project_jira(
    project_id: str,
    session: DatabaseSession,
    background_tasks: BackgroundTasks,
    max_issues: int | None = None,
    background: bool = False,
) -> dict[str, object]:
    """Sync Jira. See `sync_project_github` for why synchronous is the default."""
    if background:
        return await _start_background_sync(
            background_tasks, session, "jira", project_id, max_issues
        )
    try:
        report = await sync_jira_project(session, project_id, max_issues=max_issues)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JiraRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
            },
        ) from exc
    except JiraSyncInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Jira API request failed: {exc}") from exc
    return {"status": "synced", **asdict(report)}


@router.put(
    "/projects/{project_id}/connectors/slack",
    response_model=ProjectSummary,
    dependencies=[Depends(require_api_key)],
)
async def configure_project_slack(
    project_id: str,
    request: SlackProjectConfig,
    session: DatabaseSession,
) -> ProjectSummary:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} does not exist.")
    project.slack_channel_ids = request.channel_ids
    await session.commit()
    return _project_summary(project)


@router.post("/projects/{project_id}/sync/slack", dependencies=[Depends(require_api_key)])
async def sync_project_slack(
    project_id: str,
    session: DatabaseSession,
    background_tasks: BackgroundTasks,
    max_messages: int | None = None,
    background: bool = False,
) -> dict[str, object]:
    """Sync Slack. See `sync_project_github` for why synchronous is the default."""
    if background:
        return await _start_background_sync(
            background_tasks, session, "slack", project_id, max_messages
        )
    try:
        report = await sync_slack_project(session, project_id, max_messages=max_messages)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SlackRateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail={"message": str(exc), "retry_after_seconds": exc.retry_after_seconds},
        ) from exc
    except SlackSyncInProgressError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SlackAPIError as exc:
        # Slack reports API failures in the payload, so surface its error code rather than a
        # generic upstream failure.
        raise HTTPException(status_code=502, detail=f"Slack API error: {exc.error}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Slack API request failed: {exc}") from exc
    return {"status": "synced", **asdict(report)}


@router.get("/projects/{project_id}/sync/slack", dependencies=[Depends(require_api_key)])
async def slack_sync_status(project_id: str, session: DatabaseSession) -> dict[str, object]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} does not exist.")
    state = await session.scalar(
        select(ConnectorSyncState).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "slack",
        )
    )
    if state is None:
        return {
            "project_id": project_id,
            "slack_channel_ids": list(project.slack_channel_ids or []),
            "status": "never_synced",
        }
    return {
        "project_id": project_id,
        "slack_channel_ids": list(project.slack_channel_ids or []),
        "status": state.status,
        "last_started_at": state.last_started_at,
        "last_succeeded_at": state.last_succeeded_at,
        "last_error": state.last_error,
        "rate_limit_remaining": state.rate_limit_remaining,
    }


@router.post(
    "/projects",
    response_model=ProjectSummary,
    status_code=201,
    dependencies=[Depends(require_api_key)],
)
async def create_project(request: ProjectCreate, session: DatabaseSession) -> ProjectSummary:
    if await session.get(Project, request.id) is not None:
        raise HTTPException(status_code=409, detail="Project id already exists.")
    project = Project(**request.model_dump())
    session.add(project)
    await session.commit()
    return _project_summary(project)


@router.get(
    "/projects",
    response_model=list[ProjectSummary],
    dependencies=[Depends(require_api_key)],
)
async def projects(session: DatabaseSession) -> list[ProjectSummary]:
    stored = list((await session.scalars(select(Project).order_by(Project.name))).all())
    return [_project_summary(project) for project in stored]


@router.get(
    "/projects/{project_id}/sync/github",
    dependencies=[Depends(require_api_key)],
)
async def github_sync_status(project_id: str, session: DatabaseSession) -> dict[str, object]:
    state = await session.scalar(
        select(ConnectorSyncState).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "github",
        )
    )
    if state is None:
        return {"project_id": project_id, "status": "never_synced"}
    return {
        "project_id": project_id,
        "status": state.status,
        "last_started_at": state.last_started_at,
        "last_succeeded_at": state.last_succeeded_at,
        "last_error": state.last_error,
        "rate_limit_remaining": state.rate_limit_remaining,
        "rate_limit_reset_at": state.rate_limit_reset_at,
    }


@router.get(
    "/projects/{project_id}/sync/jira",
    dependencies=[Depends(require_api_key)],
)
async def jira_sync_status(project_id: str, session: DatabaseSession) -> dict[str, object]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} does not exist.")
    state = await session.scalar(
        select(ConnectorSyncState).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "jira",
        )
    )
    if state is None:
        return {
            "project_id": project_id,
            "jira_project_key": project.jira_project_key,
            "status": "never_synced",
        }
    return {
        "project_id": project_id,
        "jira_project_key": project.jira_project_key,
        "status": state.status,
        "last_started_at": state.last_started_at,
        "last_succeeded_at": state.last_succeeded_at,
        "last_error": state.last_error,
        "rate_limit_remaining": state.rate_limit_remaining,
        "rate_limit_reset_at": state.rate_limit_reset_at,
    }


@router.get(
    "/projects/{project_id}/timeline",
    response_model=list[TimelineItem],
    dependencies=[Depends(require_api_key)],
)
async def project_timeline(
    project_id: str,
    session: DatabaseSession,
    limit: int = 20,
) -> list[TimelineItem]:
    """Recent indexed activity for a project, drawn from synchronized source documents."""
    if await session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} does not exist.")
    documents = list(
        (
            await session.scalars(
                select(SourceDocument)
                .where(SourceDocument.project_id == project_id)
                .order_by(
                    SourceDocument.source_created_at.desc().nullslast(),
                    SourceDocument.id.desc(),
                )
                .limit(max(1, min(limit, 100)))
            )
        ).all()
    )
    return [
        TimelineItem(
            id=f"doc-{document.id}",
            # Fall back to ingestion time so an item without provider metadata still sorts.
            timestamp=(document.source_created_at or document.created_at).isoformat(),
            source_type=document.source_type,
            title=document.title,
            summary=document.content[:280],
        )
        for document in documents
    ]


@router.get("/conversations/{conversation_id}", dependencies=[Depends(require_api_key)])
async def conversation_turns(
    conversation_id: str,
    session: DatabaseSession,
    project_id: str,
    limit: int = 50,
) -> dict[str, object]:
    """The conversation as a list of turns, so a reloaded page can restore the thread."""
    try:
        turns = await load_conversation_history(
            session, conversation_id, project_id, limit=max(1, min(limit, 200))
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"conversation_id": conversation_id, "project_id": project_id, "turns": turns}


@router.get("/conversations/{conversation_id}/trace", dependencies=[Depends(require_api_key)])
async def conversation_trace(
    conversation_id: str,
    session: DatabaseSession,
) -> dict[str, object]:
    trace = await load_trace(session, conversation_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Conversation trace not found.")
    return {"conversation_id": conversation_id, "trace": trace}
