import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ConnectorSyncState, DocumentChunk, SourceDocument
from app.services.retrieval import RetrievedRecord

AUTHOR_PATTERN = re.compile(
    r"\bby\s+(.+?)(?=\s+(?:on|for|in|from)\s+(?:project|repo|repository)\b|[?,!]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LatestCommitLookup:
    status: str
    author_query: str | None
    record: RetrievedRecord | None
    sha: str | None
    author: str | None
    candidates: list[str]
    last_synced_at: datetime | None
    stale: bool


def extract_commit_author(query: str) -> str | None:
    match = AUTHOR_PATTERN.search(query.strip())
    if not match:
        return None
    author = " ".join(match.group(1).split()).strip(" '\".?!,")
    return author or None


def normalize_author_identity(author: str) -> str:
    return " ".join(author.casefold().split())


async def _sync_freshness(session: AsyncSession, project_id: str) -> tuple[datetime | None, bool]:
    last_synced_at = await session.scalar(
        select(ConnectorSyncState.last_succeeded_at).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "github",
        )
    )
    if last_synced_at is None:
        return None, True
    stale_after = timedelta(minutes=settings.github_sync_stale_after_minutes)
    return last_synced_at, datetime.now(UTC) - last_synced_at > stale_after


def _record_from_row(row) -> RetrievedRecord:
    document = row.SourceDocument
    chunk = row.DocumentChunk
    return RetrievedRecord(
        chunk_id=chunk.id,
        document_id=document.id,
        source_type="github",
        title=document.title,
        content=chunk.content,
        url=document.url,
        source_timestamp=document.source_created_at,
        authority=0.95,
        lexical_score=1.0,
        vector_score=0.0,
    )


async def latest_commit_by_author(
    session: AsyncSession, project_id: str, author_query: str
) -> LatestCommitLookup:
    normalized = normalize_author_identity(author_query)
    last_synced_at, stale = await _sync_freshness(session, project_id)
    base = (
        select(SourceDocument, DocumentChunk)
        .join(
            DocumentChunk,
            (DocumentChunk.document_id == SourceDocument.id) & (DocumentChunk.chunk_index == 0),
        )
        .where(
            SourceDocument.project_id == project_id,
            SourceDocument.source_type == "github",
        )
    )
    exact = (
        base.where(SourceDocument.author_identities.contains([normalized]))
        .order_by(desc(SourceDocument.source_created_at), desc(SourceDocument.id))
        .limit(1)
    )
    row = (await session.execute(exact)).first()
    if row is not None:
        document = row.SourceDocument
        return LatestCommitLookup(
            status="found",
            author_query=author_query,
            record=_record_from_row(row),
            sha=str(document.source_metadata.get("sha") or document.external_id),
            author=document.author,
            candidates=[],
            last_synced_at=last_synced_at,
            stale=stale,
        )

    recent_rows = (
        await session.execute(
            base.order_by(desc(SourceDocument.source_created_at), desc(SourceDocument.id)).limit(
                100
            )
        )
    ).all()
    partial_rows = [
        item
        for item in recent_rows
        if any(normalized in identity for identity in item.SourceDocument.author_identities)
    ]
    candidates = sorted(
        {item.SourceDocument.author or "Unknown author" for item in partial_rows},
        key=str.casefold,
    )
    if len({candidate.casefold() for candidate in candidates}) == 1 and partial_rows:
        row = partial_rows[0]
        document = row.SourceDocument
        return LatestCommitLookup(
            status="found",
            author_query=author_query,
            record=_record_from_row(row),
            sha=str(document.source_metadata.get("sha") or document.external_id),
            author=document.author,
            candidates=candidates,
            last_synced_at=last_synced_at,
            stale=stale,
        )
    return LatestCommitLookup(
        status="ambiguous" if candidates else "not_found",
        author_query=author_query,
        record=None,
        sha=None,
        author=None,
        candidates=candidates,
        last_synced_at=last_synced_at,
        stale=stale,
    )
