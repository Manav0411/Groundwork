"""Query-run persistence and the citation snapshot.

The audit claim this project makes is that a historical answer stays inspectable after the source
it cited has changed. That is only true if citations and evidence are copied at answer time rather
than joined to live rows at read time — a distinction invisible in code review and invisible to
every test that does not mutate a source document afterwards.
"""

import pytest
from sqlalchemy import select, update

from app.db.models import (
    Conversation,
    DocumentChunk,
    QueryCitation,
    QueryRun,
    RetrievedEvidence,
    SourceDocument,
    TraceStepRecord,
)
from app.models.schemas import Citation, EvidenceItem, QueryRequest, QueryResponse, TraceStep
from app.services.ingestion import IngestDocument, ingest_documents
from app.services.persistence import load_trace, persist_query_run
from app.services.retrieval import RetrievedRecord, hybrid_retrieve

pytestmark = pytest.mark.integration


async def _seed(session) -> RetrievedRecord:
    await ingest_documents(
        session,
        [
            IngestDocument(
                project_id="test-project",
                source_type="github",
                external_id="sha-1",
                title="Original title",
                content="Original content about the deployment rollback.",
                url="https://github.com/acme/test/commit/sha-1",
                author="Raghav Rao",
            )
        ],
        None,
    )
    (record,) = await hybrid_retrieve(session, "test-project", "deployment rollback", ollama=None)
    return record


def _response(record: RetrievedRecord, conversation_id: str = "conv-1") -> QueryResponse:
    return QueryResponse(
        conversation_id=conversation_id,
        answer="The rollback was documented [1].",
        retrieval_grade="correct",
        tools_used=["hybrid_retrieval"],
        citations=[
            Citation(
                id=1,
                source_type=record.source_type,
                title=record.title,
                url=record.url,
                timestamp=None,
            )
        ],
        evidence=[
            EvidenceItem(
                id=f"chunk-{record.chunk_id}",
                source_type=record.source_type,
                title=record.title,
                snippet=record.content[:500],
                citation_id=1,
                authority=record.authority,
            )
        ],
        unresolved_gaps=[],
        trace=[
            TraceStep(
                name="plan", status="completed", duration_ms=3, summary="Routed to retrieval."
            ),
            TraceStep(name="retrieve", status="completed", duration_ms=11, summary="8 candidates."),
        ],
    )


async def test_query_run_is_persisted_with_its_full_graph(session, project) -> None:
    record = await _seed(session)

    await persist_query_run(
        session,
        QueryRequest(query="What was the rollback?", project_id="test-project"),
        "weekly_project_brief",
        _response(record),
        [record],
    )

    conversation = await session.scalar(select(Conversation))
    assert conversation.public_id == "conv-1"
    run = await session.scalar(select(QueryRun))
    assert run.conversation_id == conversation.id
    assert run.retrieval_grade == "correct"
    assert run.tools_used == ["hybrid_retrieval"]
    citation = await session.scalar(select(QueryCitation))
    assert (citation.ordinal, citation.document_id) == (1, record.document_id)
    evidence = await session.scalar(select(RetrievedEvidence))
    assert evidence.chunk_id == record.chunk_id
    assert evidence.citation_ordinal == 1


async def test_citations_survive_the_source_changing_underneath(session, project) -> None:
    """The audit guarantee. Answer, then rewrite the source, and the record must not follow."""
    record = await _seed(session)
    await persist_query_run(
        session,
        QueryRequest(query="What was the rollback?", project_id="test-project"),
        "weekly_project_brief",
        _response(record),
        [record],
    )

    await session.execute(
        update(SourceDocument)
        .where(SourceDocument.id == record.document_id)
        .values(title="Rewritten title", content="Completely different content.")
    )
    await session.commit()
    session.expunge_all()

    citation = await session.scalar(select(QueryCitation))
    evidence = await session.scalar(select(RetrievedEvidence))
    assert citation.title == "Original title"
    assert "Original content" in evidence.snippet
    # And the link back to the live document is still intact, so the change is discoverable.
    assert citation.document_id == record.document_id


async def test_deleting_the_source_keeps_the_answer_readable(session, project) -> None:
    """`ondelete=SET NULL` — a deleted commit must not erase the answer that cited it."""
    record = await _seed(session)
    await persist_query_run(
        session,
        QueryRequest(query="What was the rollback?", project_id="test-project"),
        "weekly_project_brief",
        _response(record),
        [record],
    )

    document = await session.get(SourceDocument, record.document_id)
    await session.delete(document)
    await session.commit()
    session.expunge_all()

    citation = await session.scalar(select(QueryCitation))
    assert citation is not None
    assert citation.document_id is None
    assert citation.title == "Original title"
    assert citation.url == "https://github.com/acme/test/commit/sha-1"
    # The chunk went with the document, but the snapshotted snippet did not.
    assert await session.scalar(select(DocumentChunk)) is None
    evidence = await session.scalar(select(RetrievedEvidence))
    assert evidence.chunk_id is None
    assert "Original content" in evidence.snippet


async def test_trace_steps_are_stored_and_replayed_in_order(session, project) -> None:
    record = await _seed(session)
    await persist_query_run(
        session,
        QueryRequest(query="What was the rollback?", project_id="test-project"),
        "weekly_project_brief",
        _response(record),
        [record],
    )

    steps = await load_trace(session, "conv-1")

    assert [step["name"] for step in steps] == ["plan", "retrieve"]
    assert [step["duration_ms"] for step in steps] == [3, 11]
    assert all(step["status"] == "completed" for step in steps)


async def test_trace_sequence_is_persisted_not_inferred_from_insertion(session, project) -> None:
    record = await _seed(session)
    await persist_query_run(
        session,
        QueryRequest(query="q", project_id="test-project"),
        "weekly_project_brief",
        _response(record),
        [record],
    )

    sequences = list(
        (
            await session.scalars(
                select(TraceStepRecord.sequence).order_by(TraceStepRecord.sequence)
            )
        ).all()
    )

    assert sequences == [0, 1]


async def test_load_trace_returns_none_for_an_unknown_conversation(session, project) -> None:
    assert await load_trace(session, "conv-does-not-exist") is None


async def test_an_answer_with_no_evidence_persists_zero_citations(session, project) -> None:
    """The refusal contract, at the storage layer: no evidence must mean no citation rows."""
    response = QueryResponse(
        conversation_id="conv-empty",
        answer="No evidence was found for this question.",
        retrieval_grade="incorrect",
        tools_used=["hybrid_retrieval"],
        citations=[],
        evidence=[],
        unresolved_gaps=["No indexed source matches this question."],
        trace=[TraceStep(name="grade", status="completed", duration_ms=5, summary="Insufficient.")],
    )

    await persist_query_run(
        session,
        QueryRequest(query="What is the Sprint 24 velocity?", project_id="test-project"),
        "weekly_project_brief",
        response,
        [],
    )

    run = await session.scalar(select(QueryRun))
    assert run.retrieval_grade == "incorrect"
    assert run.unresolved_gaps
    assert await session.scalar(select(QueryCitation)) is None
    assert await session.scalar(select(RetrievedEvidence)) is None


async def test_conversations_expose_only_the_opaque_public_id(session, project) -> None:
    """Internal bigint ids are for index locality and are never handed out."""
    record = await _seed(session)
    await persist_query_run(
        session,
        QueryRequest(query="q", project_id="test-project"),
        "weekly_project_brief",
        _response(record, conversation_id="conv-abc123"),
        [record],
    )

    conversation = await session.scalar(select(Conversation))
    assert conversation.public_id == "conv-abc123"
    assert conversation.id != conversation.public_id
    assert await load_trace(session, "conv-abc123") is not None
