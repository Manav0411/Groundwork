"""Hybrid retrieval against a real database.

`tests/test_retrieval.py` compiles the statement and inspects the SQL string. That catches a
malformed query; it cannot catch a query that runs and returns the wrong rows — which is exactly
the failure Phase 2 spent its time on, where `websearch_to_tsquery` produced valid SQL that scored
0 for questions whose words were plainly in the corpus.

Every embedding here is written by hand so the expected cosine ordering is exact.
"""

import pytest

from app.services.ingestion import IngestDocument, ingest_documents
from app.services.retrieval import hybrid_retrieve

from .conftest import StubEmbedder, unit_vector

pytestmark = pytest.mark.integration


def _document(external_id: str, content: str, *, project_id: str = "test-project", **overrides):
    values = {
        "project_id": project_id,
        "source_type": "github",
        "external_id": external_id,
        "title": f"Document {external_id}",
        "content": content,
        "url": f"https://example.test/{external_id}",
    }
    values.update(overrides)
    return IngestDocument(**values)


async def test_partial_term_question_still_matches(session, project) -> None:
    """The Phase 2 defect: every query term had to appear in one chunk for any lexical match.

    The question below shares only "deployment" and "vercel" with the corpus. Under the AND-ing
    `websearch_to_tsquery` this returned nothing at all.
    """
    await ingest_documents(
        session,
        [_document("a", "Fixed the vercel deployment routing rules.")],
        None,
    )

    records = await hybrid_retrieve(
        session, "test-project", "What deployment and vercel routing work was done?", ollama=None
    )

    assert [record.document_id for record in records]
    assert records[0].lexical_score > 0


async def test_all_stopword_question_returns_empty_instead_of_raising(session, project) -> None:
    """`to_tsquery('')` raises a syntax error in PostgreSQL; the `nullif` guard yields NULL."""
    await ingest_documents(session, [_document("a", "Some indexed content.")], None)

    assert await hybrid_retrieve(session, "test-project", "what is it", ollama=None) == []


async def test_retrieval_is_scoped_to_the_project(session, project, other_project) -> None:
    """The fabrication bug this guards against was one project answering with another's evidence."""
    await ingest_documents(
        session,
        [
            _document("mine", "Postgres connection pooling was tuned."),
            _document(
                "theirs", "Postgres connection pooling was tuned.", project_id="other-project"
            ),
        ],
        None,
    )

    records = await hybrid_retrieve(
        session, "test-project", "postgres connection pooling", ollama=None
    )

    assert len(records) == 1
    assert records[0].title == "Document mine"


async def test_vector_retrieval_finds_documents_with_no_lexical_overlap(session, project) -> None:
    """The paraphrase case: the answer shares no vocabulary with the question."""
    content = "The team pinned bcrypt to version four."
    question = "why is the password hashing library held back"
    embedder = StubEmbedder({content: unit_vector(1, 0), question: unit_vector(1, 0)})
    await ingest_documents(session, [_document("a", content)], embedder)

    records = await hybrid_retrieve(session, "test-project", question, ollama=embedder)

    assert len(records) == 1
    assert records[0].lexical_score == 0, "No shared terms, so this is a pure vector hit."
    assert records[0].vector_score == pytest.approx(1.0, abs=1e-6)


async def test_lexical_rank_breaks_ties_between_adjacent_vector_ranks(session, project) -> None:
    """The calibrated weights make lexical a tie-breaker, not a driver. This is that, measured.

    `near` is the closer vector match but shares no query terms. `matching` is slightly further
    away and does share them. Under RRF with lexical 0.15 / vector 1.0 and k=10:
        matching = 0.15*(1/11) + 1.0*(1/12) = 0.0970
        near     = 0                + 1.0*(1/11) = 0.0909
    so the lexical signal is exactly strong enough to flip one vector rank and no more.
    """
    question = "rollback procedure"
    matching = "Documented the rollback procedure for failed deploys."
    near = "Unrelated prose with no shared vocabulary whatsoever."
    embedder = StubEmbedder(
        {
            question: unit_vector(1, 0),
            near: unit_vector(1, 0),
            matching: unit_vector(0.99, 0.14),
        }
    )
    await ingest_documents(
        session, [_document("near", near), _document("matching", matching)], embedder
    )

    records = await hybrid_retrieve(session, "test-project", question, ollama=embedder)

    assert [record.title for record in records] == ["Document matching", "Document near"]
    # Control: without the lexical hit the pure-vector ordering is the other way round.
    vector_only = await hybrid_retrieve(
        session, "test-project", question, ollama=embedder, lexical_weight=0.0
    )
    assert [record.title for record in vector_only] == ["Document near", "Document matching"]


async def test_one_document_contributes_only_its_best_chunk(session, project) -> None:
    """A long document must not crowd the result set out from under other sources."""
    long_content = "deployment " * 400
    await ingest_documents(
        session,
        [
            _document("long", long_content),
            _document("short", "A short deployment note."),
        ],
        None,
    )

    records = await hybrid_retrieve(session, "test-project", "deployment", limit=2, ollama=None)

    assert len({record.document_id for record in records}) == len(records)
    assert len(records) == 2, "Both documents should be represented, not two chunks of one."


async def test_missing_embeddings_degrade_to_lexical_only(session, project) -> None:
    """Documented contract: no embeddings means full-text still answers, with vector_score 0."""
    await ingest_documents(session, [_document("a", "Retry logic for the upload flow.")], None)

    records = await hybrid_retrieve(session, "test-project", "retry logic upload", ollama=None)

    assert len(records) == 1
    assert records[0].vector_score == 0
    assert records[0].lexical_score > 0


async def test_candidate_depth_bounds_the_vector_side(session, project) -> None:
    """Before this bound the vector CTE admitted every embedded chunk in the project."""
    question = "anything"
    embedder = StubEmbedder({question: unit_vector(1, 0)})
    await ingest_documents(
        session,
        [_document(str(index), f"Distinct content number {index}.") for index in range(5)],
        embedder,
    )

    records = await hybrid_retrieve(
        session, "test-project", question, ollama=embedder, candidate_depth=2
    )

    assert len(records) == 2


async def test_authority_defaults_when_metadata_omits_it(session, project) -> None:
    await ingest_documents(
        session,
        [
            _document("plain", "Content without an authority score."),
            _document("rated", "Content carrying an authority score.", metadata={"authority": 0.4}),
        ],
        None,
    )

    records = await hybrid_retrieve(session, "test-project", "content authority", ollama=None)
    by_title = {record.title: record.authority for record in records}

    assert by_title["Document plain"] == 0.8
    assert by_title["Document rated"] == 0.4


async def test_records_carry_the_fields_citations_are_built_from(session, project) -> None:
    """Citations need a real URL and timestamp; a retrieval that drops them cannot be cited."""
    from datetime import UTC, datetime

    await ingest_documents(
        session,
        [
            _document(
                "a",
                "Migration applied to the staging database.",
                source_created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            )
        ],
        None,
    )

    (record,) = await hybrid_retrieve(session, "test-project", "staging migration", ollama=None)

    assert record.url == "https://example.test/a"
    assert record.source_timestamp == datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    assert record.source_type == "github"
    assert record.chunk_id > 0 and record.document_id > 0
