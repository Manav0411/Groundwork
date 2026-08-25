"""Ingestion against a real database.

The contract these tests hold to: a sync is an atomic upsert where unchanged content keeps its
chunks, and changed content is re-chunked and re-embedded. Re-embedding is the expensive half of a
sync, so "unchanged content writes nothing" is a performance guarantee as much as a correctness one
— and nothing verified it until now.
"""

import pytest
from sqlalchemy import select

from app.db.models import DocumentChunk, Project, SourceDocument
from app.services.ingestion import IngestDocument, ingest_documents, upsert_project

from .conftest import FailingEmbedder, StubEmbedder, unit_vector

pytestmark = pytest.mark.integration


def _document(
    content: str = "Initial commit content about deployment.", **overrides
) -> IngestDocument:
    values = {
        "project_id": "test-project",
        "source_type": "github",
        "external_id": "sha-1",
        "title": "Initial commit",
        "content": content,
        "url": "https://github.com/acme/test/commit/sha-1",
        "author": "Raghav Rao",
        "author_identities": ["raghav rao", "raghav-dev"],
    }
    values.update(overrides)
    return IngestDocument(**values)


async def _chunks(session, document_id: int) -> list[DocumentChunk]:
    return list(
        (
            await session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index)
            )
        ).all()
    )


async def test_first_ingest_writes_document_and_chunks(session, project) -> None:
    stats = await ingest_documents(session, [_document()], StubEmbedder())

    assert stats == {"documents": 1, "chunks": 1, "embedded": 1}
    document = await session.scalar(select(SourceDocument))
    assert document.external_id == "sha-1"
    assert document.author_identities == ["raghav rao", "raghav-dev"]
    chunks = await _chunks(session, document.id)
    assert len(chunks) == 1
    assert chunks[0].embedding is not None


async def test_unchanged_content_writes_no_chunks_and_keeps_chunk_ids(session, project) -> None:
    """The idempotency guarantee. A re-sync of unchanged content must not re-embed anything."""
    embedder = StubEmbedder()
    await ingest_documents(session, [_document()], embedder)
    document_id = (await session.scalar(select(SourceDocument))).id
    original_ids = [chunk.id for chunk in await _chunks(session, document_id)]
    embed_calls_after_first = len(embedder.calls)

    stats = await ingest_documents(session, [_document()], embedder)

    assert stats["chunks"] == 0
    assert stats["embedded"] == 0
    assert [chunk.id for chunk in await _chunks(session, document_id)] == original_ids
    # No embedding call at all: the content hash short-circuits before the model is consulted.
    assert len(embedder.calls) == embed_calls_after_first


async def test_changed_content_replaces_chunks(session, project) -> None:
    await ingest_documents(session, [_document()], StubEmbedder())
    document_id = (await session.scalar(select(SourceDocument))).id
    original_ids = {chunk.id for chunk in await _chunks(session, document_id)}

    stats = await ingest_documents(
        session,
        [_document(content="Rewritten content about the rollback procedure.")],
        StubEmbedder(),
    )

    assert stats["chunks"] == 1
    chunks = await _chunks(session, document_id)
    # The document row is reused; only its chunks are rebuilt.
    assert {chunk.id for chunk in chunks}.isdisjoint(original_ids)
    assert "rollback" in chunks[0].content


async def test_repeated_external_id_upserts_rather_than_duplicating(session, project) -> None:
    """`uq_source_documents_project_id` makes a re-sync an update rather than a second row."""
    await ingest_documents(session, [_document(title="First title")], StubEmbedder())
    await ingest_documents(
        session,
        [_document(title="Second title", content="Different content entirely.")],
        StubEmbedder(),
    )

    documents = list((await session.scalars(select(SourceDocument))).all())
    assert len(documents) == 1
    assert documents[0].title == "Second title"


async def test_same_external_id_in_a_different_project_is_a_separate_document(
    session, project, other_project
) -> None:
    """The uniqueness constraint is scoped per project; two repos can share a commit SHA."""
    await ingest_documents(session, [_document()], StubEmbedder())
    await ingest_documents(session, [_document(project_id="other-project")], StubEmbedder())

    assert len(list((await session.scalars(select(SourceDocument))).all())) == 2


async def test_embedding_failure_still_stores_retrievable_chunks(session, project) -> None:
    """Documented contract: retrieval degrades to full-text when the embedding model is offline."""
    embedder = FailingEmbedder()

    stats = await ingest_documents(session, [_document()], embedder)

    assert embedder.calls == 1
    assert stats["chunks"] == 1
    assert stats["embedded"] == 0
    document_id = (await session.scalar(select(SourceDocument))).id
    chunks = await _chunks(session, document_id)
    assert chunks[0].embedding is None
    # Lexical retrieval still works, because `search_vector` never depended on the model.
    assert chunks[0].search_vector is not None


async def test_null_embeddings_are_backfilled_without_rechunking(session, project) -> None:
    """A sync that ran while Ollama was down must be repairable by a later sync."""
    await ingest_documents(session, [_document()], FailingEmbedder())
    document_id = (await session.scalar(select(SourceDocument))).id
    original_ids = [chunk.id for chunk in await _chunks(session, document_id)]

    stats = await ingest_documents(session, [_document()], StubEmbedder())

    assert stats["chunks"] == 0, "Content is unchanged, so nothing should be re-chunked."
    assert stats["embedded"] == 1
    chunks = await _chunks(session, document_id)
    assert [chunk.id for chunk in chunks] == original_ids
    assert chunks[0].embedding is not None


async def test_search_vector_is_generated_by_postgres(session, project) -> None:
    """`search_vector` is a stored generated column; application code never writes it."""
    await ingest_documents(
        session, [_document(content="Deployment pipeline hardening for the staging cluster.")], None
    )

    chunk = await session.scalar(select(DocumentChunk))
    lexemes = str(chunk.search_vector)
    # English stemming is the point: "hardening" is indexed as "harden".
    assert "harden" in lexemes
    assert "deploy" in lexemes


async def test_long_content_produces_multiple_ordered_chunks(session, project) -> None:
    stats = await ingest_documents(
        session, [_document(content="deployment " * 400)], StubEmbedder()
    )

    assert stats["chunks"] > 1
    document_id = (await session.scalar(select(SourceDocument))).id
    chunks = await _chunks(session, document_id)
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.embedding is not None for chunk in chunks)


async def test_registered_vectors_are_stored_verbatim(session, project) -> None:
    """Guards the fixture itself: ranking tests are meaningless if vectors are not what we set."""
    content = "Vector fidelity check."
    expected = unit_vector(1, 0, 0)

    await ingest_documents(session, [_document(content=content)], StubEmbedder({content: expected}))

    chunk = await session.scalar(select(DocumentChunk))
    assert chunk.embedding == pytest.approx(expected, abs=1e-6)


async def test_upsert_project_updates_rather_than_conflicting(session) -> None:
    initial = Project(id="p1", name="Original", repo="acme/one", status="Active", health="green")
    await upsert_project(session, initial)
    await upsert_project(
        session, Project(id="p1", name="Renamed", repo="acme/one", status="Paused", health="yellow")
    )

    projects = list((await session.scalars(select(Project))).all())
    assert len(projects) == 1
    assert (projects[0].name, projects[0].health) == ("Renamed", "yellow")
