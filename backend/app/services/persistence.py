from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Conversation,
    QueryCitation,
    QueryRun,
    RetrievedEvidence,
    TraceStepRecord,
)
from app.models.schemas import ConversationTurn, QueryRequest, QueryResponse
from app.services.retrieval import RetrievedRecord


class ConversationNotFoundError(LookupError):
    """Raised when a conversation id is unknown, or belongs to a different project."""


async def _conversation_for(
    session: AsyncSession, public_id: str, project_id: str
) -> Conversation | None:
    """Load a conversation, refusing one that belongs to another project.

    Scoping is enforced here rather than trusted from the caller: an id leaking across projects
    would let one project's history steer another project's answers.
    """
    conversation = await session.scalar(
        select(Conversation).where(Conversation.public_id == public_id)
    )
    if conversation is None:
        return None
    if conversation.project_id != project_id:
        raise ConversationNotFoundError(
            f"Conversation {public_id!r} does not belong to project {project_id!r}."
        )
    return conversation


async def load_conversation_history(
    session: AsyncSession,
    public_id: str,
    project_id: str,
    *,
    limit: int = 5,
) -> list[ConversationTurn]:
    """The most recent turns, oldest first.

    Bounded so the resolution prompt cannot grow with the conversation. Only the question and
    answer text are returned — never evidence or citations, because history must not be able to
    become a source for a later answer.
    """
    conversation = await _conversation_for(session, public_id, project_id)
    if conversation is None:
        raise ConversationNotFoundError(f"Conversation {public_id!r} does not exist.")

    runs = list(
        (
            await session.scalars(
                select(QueryRun)
                .where(QueryRun.conversation_id == conversation.id)
                .order_by(QueryRun.created_at.desc(), QueryRun.id.desc())
                .limit(limit)
            )
        ).all()
    )
    return [
        ConversationTurn(
            query=run.query,
            resolved_query=run.resolved_query,
            answer=run.answer,
            retrieval_grade=run.retrieval_grade,
            created_at=run.created_at.isoformat() if run.created_at else None,
        )
        for run in reversed(runs)
    ]


async def persist_query_run(
    session: AsyncSession,
    request: QueryRequest,
    query_type: str,
    response: QueryResponse,
    records: list[RetrievedRecord],
) -> None:
    # A conversation is created once and appended to thereafter. Before multi-turn this always
    # inserted, which is why every conversation was exactly one question long.
    conversation = await _conversation_for(session, response.conversation_id, request.project_id)
    if conversation is None:
        conversation = Conversation(
            public_id=response.conversation_id,
            project_id=request.project_id,
        )
        session.add(conversation)
    await session.flush()

    query_run = QueryRun(
        conversation_id=conversation.id,
        query=request.query,
        resolved_query=response.resolved_query,
        query_type=query_type,
        answer=response.answer,
        retrieval_grade=response.retrieval_grade,
        tools_used=response.tools_used,
        unresolved_gaps=response.unresolved_gaps,
    )
    session.add(query_run)
    await session.flush()

    record_by_chunk = {record.chunk_id: record for record in records}
    for citation in response.citations:
        evidence = next(
            (item for item in response.evidence if item.citation_id == citation.id), None
        )
        chunk_id = None
        if evidence and evidence.id.startswith("chunk-"):
            chunk_id = int(evidence.id.removeprefix("chunk-"))
        record = record_by_chunk.get(chunk_id) if chunk_id is not None else None
        session.add(
            QueryCitation(
                query_run_id=query_run.id,
                document_id=record.document_id if record else None,
                ordinal=citation.id,
                source_type=citation.source_type,
                title=citation.title,
                url=citation.url,
                source_timestamp=record.source_timestamp if record else None,
            )
        )
        if evidence:
            session.add(
                RetrievedEvidence(
                    query_run_id=query_run.id,
                    chunk_id=chunk_id,
                    citation_ordinal=citation.id,
                    source_type=evidence.source_type,
                    title=evidence.title,
                    snippet=evidence.snippet,
                    authority=evidence.authority,
                    lexical_score=record.lexical_score if record else 0,
                    vector_score=record.vector_score if record else 0,
                )
            )

    session.add_all(
        [
            TraceStepRecord(
                query_run_id=query_run.id,
                sequence=sequence,
                name=step.name,
                status=step.status,
                duration_ms=step.duration_ms,
                summary=step.summary,
            )
            for sequence, step in enumerate(response.trace)
        ]
    )
    await session.commit()


async def load_trace(session: AsyncSession, public_id: str) -> list[dict[str, object]] | None:
    statement = (
        select(TraceStepRecord)
        .join(QueryRun, QueryRun.id == TraceStepRecord.query_run_id)
        .join(Conversation, Conversation.id == QueryRun.conversation_id)
        .where(Conversation.public_id == public_id)
        .order_by(TraceStepRecord.sequence)
    )
    steps = list((await session.scalars(statement)).all())
    if not steps:
        return None
    return [
        {
            "name": step.name,
            "status": step.status,
            "duration_ms": step.duration_ms,
            "summary": step.summary,
        }
        for step in steps
    ]
