import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ConnectorSyncState, DocumentChunk, SourceDocument
from app.services.retrieval import RetrievedRecord

ISSUE_KEY_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]{1,19}-\d+)\b", re.IGNORECASE)
ASSIGNEE_PATTERN = re.compile(
    r"\bassigned\s+to\s+(.+?)(?=\s+(?:on|for|in)\s+project\b|[?,!.]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JiraIssueRecord:
    key: str
    summary: str
    status: str
    status_category: str
    priority: str | None
    assignee: str | None
    issue_type: str
    record: RetrievedRecord


@dataclass(frozen=True)
class JiraProjectStatus:
    """Issue counts for a whole project, plus the work that is not finished."""

    total: int
    by_category: dict[str, int]
    outstanding: list[JiraIssueRecord]
    last_synced_at: datetime | None
    stale: bool

    @property
    def done(self) -> int:
        return self.by_category.get("done", 0)

    @property
    def complete(self) -> bool:
        """True only when there is work and all of it is done.

        An empty project is not a complete one, and saying so is the difference between an answer
        and a coincidence.
        """
        return self.total > 0 and self.done == self.total


@dataclass(frozen=True)
class JiraLookup:
    status: str
    issues: list[JiraIssueRecord]
    candidates: list[str]
    last_synced_at: datetime | None
    stale: bool


def extract_issue_key(query: str) -> str | None:
    match = ISSUE_KEY_PATTERN.search(query)
    return match.group(1).upper() if match else None


def extract_assignee(query: str) -> str | None:
    match = ASSIGNEE_PATTERN.search(query.strip())
    if not match:
        return None
    value = " ".join(match.group(1).split()).strip(" '\".?!,")
    return value or None


async def _sync_freshness(session: AsyncSession, project_id: str) -> tuple[datetime | None, bool]:
    last_synced_at = await session.scalar(
        select(ConnectorSyncState.last_succeeded_at).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "jira",
        )
    )
    if last_synced_at is None:
        return None, True
    stale_after = timedelta(minutes=settings.jira_sync_stale_after_minutes)
    return last_synced_at, datetime.now(UTC) - last_synced_at > stale_after


def _record_from_row(row) -> RetrievedRecord:
    document = row.SourceDocument
    chunk = row.DocumentChunk
    return RetrievedRecord(
        chunk_id=chunk.id,
        document_id=document.id,
        source_type="jira",
        title=document.title,
        content=chunk.content,
        url=document.url,
        source_timestamp=document.source_created_at,
        authority=0.95,
        lexical_score=1.0,
        vector_score=0.0,
    )


def _issue_from_row(row) -> JiraIssueRecord:
    metadata = row.SourceDocument.source_metadata
    return JiraIssueRecord(
        key=str(metadata.get("key") or row.SourceDocument.external_id),
        summary=str(metadata.get("summary") or row.SourceDocument.title),
        status=str(metadata.get("status") or "Unknown"),
        status_category=str(metadata.get("status_category") or "undefined"),
        priority=str(metadata["priority"]) if metadata.get("priority") else None,
        assignee=str(metadata["assignee"]) if metadata.get("assignee") else None,
        issue_type=str(metadata.get("issue_type") or "Issue"),
        record=_record_from_row(row),
    )


def _base_query(project_id: str):
    return (
        select(SourceDocument, DocumentChunk)
        .join(
            DocumentChunk,
            (DocumentChunk.document_id == SourceDocument.id) & (DocumentChunk.chunk_index == 0),
        )
        .where(
            SourceDocument.project_id == project_id,
            SourceDocument.source_type == "jira",
        )
    )


async def jira_issue_by_key(session: AsyncSession, project_id: str, issue_key: str) -> JiraLookup:
    last_synced_at, stale = await _sync_freshness(session, project_id)
    row = (
        await session.execute(
            _base_query(project_id).where(
                func.upper(SourceDocument.external_id) == issue_key.upper()
            )
        )
    ).first()
    return JiraLookup(
        status="found" if row else "not_found",
        issues=[_issue_from_row(row)] if row else [],
        candidates=[],
        last_synced_at=last_synced_at,
        stale=stale,
    )


async def open_jira_blockers(session: AsyncSession, project_id: str) -> JiraLookup:
    last_synced_at, stale = await _sync_freshness(session, project_id)
    metadata = SourceDocument.source_metadata
    rows = (
        await session.execute(
            _base_query(project_id)
            .where(func.lower(metadata["status_category"].astext) != "done")
            .where(
                or_(
                    func.lower(metadata["priority"].astext).in_(["highest", "blocker"]),
                    metadata["labels"].contains(["blocked"]),
                )
            )
            .order_by(desc(SourceDocument.source_created_at), SourceDocument.external_id)
            .limit(20)
        )
    ).all()
    return JiraLookup(
        status="found" if rows else "not_found",
        issues=[_issue_from_row(row) for row in rows],
        candidates=[],
        last_synced_at=last_synced_at,
        stale=stale,
    )


async def jira_issues_by_assignee(
    session: AsyncSession, project_id: str, assignee_query: str
) -> JiraLookup:
    normalized = " ".join(assignee_query.casefold().split())
    last_synced_at, stale = await _sync_freshness(session, project_id)
    base = _base_query(project_id)
    exact_rows = (
        await session.execute(
            base.where(SourceDocument.author_identities.contains([normalized])).order_by(
                desc(SourceDocument.source_created_at), SourceDocument.external_id
            )
        )
    ).all()
    if exact_rows:
        return JiraLookup(
            status="found",
            issues=[_issue_from_row(row) for row in exact_rows[:20]],
            candidates=[],
            last_synced_at=last_synced_at,
            stale=stale,
        )
    recent_rows = (
        await session.execute(base.order_by(desc(SourceDocument.source_created_at)).limit(200))
    ).all()
    partial_rows = [
        row
        for row in recent_rows
        if any(normalized in identity for identity in row.SourceDocument.author_identities)
    ]
    candidates = sorted(
        {
            str(row.SourceDocument.source_metadata.get("assignee"))
            for row in partial_rows
            if row.SourceDocument.source_metadata.get("assignee")
        },
        key=str.casefold,
    )
    if len({candidate.casefold() for candidate in candidates}) == 1 and partial_rows:
        return JiraLookup(
            status="found",
            issues=[_issue_from_row(row) for row in partial_rows[:20]],
            candidates=candidates,
            last_synced_at=last_synced_at,
            stale=stale,
        )
    return JiraLookup(
        status="ambiguous" if candidates else "not_found",
        issues=[],
        candidates=candidates,
        last_synced_at=last_synced_at,
        stale=stale,
    )


async def jira_project_status(session: AsyncSession, project_id: str) -> JiraProjectStatus:
    """Count the project's issues by status category.

    This exists because quantifier questions have no answer in the RAG path. The grader asks
    whether a *passage* supports the answer, but "are all the tasks complete?" is answered by the
    *set* — no single chunk states it, so all of them are correctly rejected and the question is
    refused. Loosening the grader to fix that would weaken the property it exists for.

    Counting rows answers it exactly instead, on the deterministic path, with no model involved.
    The outstanding issues come back too so the answer can cite the specific work that is not done
    rather than asserting a bare number.
    """
    last_synced_at, stale = await _sync_freshness(session, project_id)
    category = func.lower(SourceDocument.source_metadata["status_category"].astext)
    counts = (
        await session.execute(
            select(category, func.count())
            .where(
                SourceDocument.project_id == project_id,
                SourceDocument.source_type == "jira",
            )
            .group_by(category)
        )
    ).all()
    by_category = {str(name or "unknown"): int(total) for name, total in counts}

    outstanding = (
        await session.execute(
            _base_query(project_id)
            .where(category != "done")
            .order_by(desc(SourceDocument.source_created_at), SourceDocument.external_id)
            .limit(20)
        )
    ).all()

    return JiraProjectStatus(
        total=sum(by_category.values()),
        by_category=by_category,
        outstanding=[_issue_from_row(row) for row in outstanding],
        last_synced_at=last_synced_at,
        stale=stale,
    )
