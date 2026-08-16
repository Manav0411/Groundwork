from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Conversation,
    QueryCitation,
    QueryRun,
    RetrievedEvidence,
    TraceStepRecord,
)
from app.models.schemas import QueryRequest, QueryResponse
from app.services.retrieval import RetrievedRecord


async def persist_query_run(
    session: AsyncSession,
    request: QueryRequest,
    query_type: str,
    response: QueryResponse,
    records: list[RetrievedRecord],
) -> None:
    conversation = Conversation(
        public_id=response.conversation_id,
        project_id=request.project_id,
    )
    session.add(conversation)
    await session.flush()

    query_run = QueryRun(
        conversation_id=conversation.id,
        query=request.query,
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
