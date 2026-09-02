"""Exact-answer lookups over indexed Slack threads.

Recency questions about Slack had no deterministic path. "What was the last
conversation on slack?" fell through to hybrid retrieval, which ranks by
semantic similarity — and "the last conversation" has no semantic content to
match on, so the grader correctly rejected every chunk and the run refused. The
answer was one ordered SQL read away, exactly as it was for commits and issues.

Nothing needed re-ingesting. `source_created_at` already holds the thread's
newest message, so ordering by it reflects activity rather than when a thread
began, and the channel name is already in metadata.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ConnectorSyncState, DocumentChunk, SourceDocument
from app.services.retrieval import RetrievedRecord

# Slack's own channel naming: lowercase, digits, hyphen, underscore, period.
# Three forms, tried in order of how explicit they are. A bare word after "in"
# is not included: "in production" names a topic, not a channel.
_NAME = r"([a-z0-9][a-z0-9._-]{1,79})"
CHANNEL_PATTERNS = (
    re.compile(rf"#{_NAME}", re.IGNORECASE),  # #ops
    re.compile(rf"\bchannel\s+#?{_NAME}", re.IGNORECASE),  # channel ops
    re.compile(rf"\bin\s+(?:the\s+)?{_NAME}\s+channel\b", re.IGNORECASE),  # in the ops channel
)
# Words that follow the same grammar but name the source or nothing at all.
_NOT_A_CHANNEL = {"slack", "the", "a", "an", "this", "that", "channel"}
SLACK_SUBJECT_PATTERN = re.compile(
    r"\b(?:slack|threads?|conversations?|discussions?|messages?)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class SlackThreadRecord:
    channel_name: str
    headline: str
    author: str | None
    participants: list[str]
    message_count: int
    latest_at: datetime | None
    permalink: str | None
    record: RetrievedRecord


@dataclass(frozen=True)
class SlackLookup:
    """`available` is the thread count, so an out-of-range position can say how many exist."""

    status: str  # found | not_found | out_of_range
    threads: list[SlackThreadRecord]
    available: int
    channel: str | None
    last_synced_at: datetime | None
    stale: bool


def extract_slack_channel(query: str) -> str | None:
    """The channel a question is scoped to, if it names one.

    Deliberately conservative: only an explicit `#name` or a "channel" qualifier
    counts, because a bare noun after "in" is far more often a topic.
    """
    for pattern in CHANNEL_PATTERNS:
        for match in pattern.finditer(query):
            name = match.group(1).lower()
            if name not in _NOT_A_CHANNEL:
                return name
    return None


async def _sync_freshness(session: AsyncSession, project_id: str) -> tuple[datetime | None, bool]:
    last_synced_at = await session.scalar(
        select(ConnectorSyncState.last_succeeded_at).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "slack",
        )
    )
    if last_synced_at is None:
        return None, True
    stale_after = timedelta(minutes=settings.slack_sync_stale_after_minutes)
    return last_synced_at, datetime.now(UTC) - last_synced_at > stale_after


def _record_from_row(row) -> RetrievedRecord:
    document = row.SourceDocument
    chunk = row.DocumentChunk
    return RetrievedRecord(
        chunk_id=chunk.id,
        document_id=document.id,
        source_type="slack",
        title=document.title,
        content=chunk.content,
        url=document.url,
        source_timestamp=document.source_created_at,
        authority=0.95,
        lexical_score=1.0,
        vector_score=0.0,
    )


def _thread_from_row(row) -> SlackThreadRecord:
    document = row.SourceDocument
    metadata = document.source_metadata or {}
    title = document.title or ""
    # Titles are stored as "#channel — headline"; fall back to the whole title
    # rather than an empty string if that shape ever changes.
    headline = title.split(" — ", 1)[1] if " — " in title else title
    participants = metadata.get("participants")
    return SlackThreadRecord(
        channel_name=str(metadata.get("channel_name") or "unknown"),
        headline=headline,
        author=document.author,
        participants=[str(p) for p in participants] if isinstance(participants, list) else [],
        message_count=int(metadata.get("message_count") or 0),
        latest_at=document.source_created_at,
        permalink=str(metadata["permalink"]) if metadata.get("permalink") else document.url,
        record=_record_from_row(row),
    )


def _base_query(project_id: str, channel: str | None):
    statement = (
        select(SourceDocument, DocumentChunk)
        .join(
            DocumentChunk,
            (DocumentChunk.document_id == SourceDocument.id) & (DocumentChunk.chunk_index == 0),
        )
        .where(
            SourceDocument.project_id == project_id,
            SourceDocument.source_type == "slack",
        )
    )
    if channel:
        statement = statement.where(
            func.lower(SourceDocument.source_metadata["channel_name"].astext) == channel.lower()
        )
    return statement


async def latest_slack_threads(
    session: AsyncSession,
    project_id: str,
    *,
    channel: str | None = None,
    offset: int = 0,
    limit: int = 1,
) -> SlackLookup:
    """The Nth-newest thread, by most recent message.

    Ordering is on `source_created_at`, which the ingester sets to the thread's
    latest message — so a long-running thread that was replied to today counts
    as more recent than one started yesterday and abandoned.
    """
    last_synced_at, stale = await _sync_freshness(session, project_id)

    available = (
        await session.scalar(
            select(func.count())
            .select_from(_base_query(project_id, channel).subquery())
        )
    ) or 0

    if available == 0:
        return SlackLookup(
            status="not_found",
            threads=[],
            available=0,
            channel=channel,
            last_synced_at=last_synced_at,
            stale=stale,
        )
    if offset >= available:
        return SlackLookup(
            status="out_of_range",
            threads=[],
            available=available,
            channel=channel,
            last_synced_at=last_synced_at,
            stale=stale,
        )

    rows = (
        await session.execute(
            _base_query(project_id, channel)
            .order_by(
                desc(SourceDocument.source_created_at).nulls_last(),
                desc(SourceDocument.id),
            )
            .offset(offset)
            .limit(max(1, min(limit, 10)))
        )
    ).all()

    return SlackLookup(
        status="found" if rows else "not_found",
        threads=[_thread_from_row(row) for row in rows],
        available=available,
        channel=channel,
        last_synced_at=last_synced_at,
        stale=stale,
    )
