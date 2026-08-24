from dataclasses import asdict
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import run_agent
from app.connectors.github import GitHubRateLimitError
from app.connectors.jira import JiraRateLimitError
from app.connectors.slack import SlackAPIError, SlackRateLimitError
from app.core.security import require_api_key
from app.db.models import ConnectorSyncState, Project, SourceDocument
from app.db.session import get_optional_session, get_session
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
from app.services.llm import OllamaClient
from app.services.persistence import load_trace
from app.services.slack_sync import SlackSyncInProgressError, sync_slack_project

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
    health = await OllamaClient().health()
    return health.__dict__


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
    return await run_agent(request, session)


@router.post("/ingest/workspace", dependencies=[Depends(require_api_key)])
async def ingest_workspace(session: DatabaseSession) -> dict[str, object]:
    stats = await seed_synthetic_workspace(session)
    return {"status": "loaded", **stats}


@router.post("/sync/github", dependencies=[Depends(require_api_key)])
async def sync_github(
    session: DatabaseSession,
    project_id: str = "project-atlas",
    max_commits: int | None = None,
) -> dict[str, object]:
    return await _sync_github_project(session, project_id, max_commits)


@router.post("/projects/{project_id}/sync/github", dependencies=[Depends(require_api_key)])
async def sync_project_github(
    project_id: str,
    session: DatabaseSession,
    max_commits: int | None = None,
) -> dict[str, object]:
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
    max_issues: int | None = None,
) -> dict[str, object]:
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
    max_messages: int | None = None,
) -> dict[str, object]:
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


@router.get("/conversations/{conversation_id}/trace", dependencies=[Depends(require_api_key)])
async def conversation_trace(
    conversation_id: str,
    session: DatabaseSession,
) -> dict[str, object]:
    trace = await load_trace(session, conversation_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Conversation trace not found.")
    return {"conversation_id": conversation_id, "trace": trace}
