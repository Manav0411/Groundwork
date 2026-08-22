from sqlalchemy.dialects import postgresql

from app.services.retrieval import (
    CANDIDATE_DEPTH,
    LEXICAL_WEIGHT,
    RRF_K,
    VECTOR_WEIGHT,
    RetrievedRecord,
    _or_tsquery,
    records_to_response,
)


def _compiled(expression):
    # `literal_binds` cannot render the `regconfig` type, so inspect the bound parameters instead.
    return expression.compile(dialect=postgresql.dialect())


def test_tsquery_ors_terms_instead_of_anding_them() -> None:
    """The defect this fixes: every query term had to appear in one chunk for any lexical match."""
    compiled = _compiled(_or_tsquery("What deployment and vercel routing work was done?"))
    sql = str(compiled)

    assert "tsvector_to_array" in sql
    assert "array_to_string" in sql
    # The separator is what turns `a & b & c` into `a | b | c`.
    assert " | " in compiled.params.values()
    assert "websearch_to_tsquery" not in sql
    assert "plainto_tsquery" not in sql


def test_tsquery_guards_the_all_stopword_query() -> None:
    """`to_tsquery('')` raises a syntax error in PostgreSQL; NULL simply never matches."""
    compiled = _compiled(_or_tsquery("what is it"))

    assert "nullif" in str(compiled).lower()
    assert "" in compiled.params.values()


def test_candidate_depth_bounds_both_retrievers() -> None:
    """The original filter admitted every embedded chunk, making the query nearly irrelevant."""
    assert 0 < CANDIDATE_DEPTH <= 200


def test_fusion_favours_the_measured_stronger_retriever() -> None:
    """Calibrated on evals/retrieval_dataset.jsonl; equal weighting cost 0.115 MRR there."""
    assert VECTOR_WEIGHT > LEXICAL_WEIGHT
    assert RRF_K > 0
    # A high k relative to candidate depth flattens rank differences to near-nothing, which is what
    # let weak double-matches outrank strong single-matches.
    assert RRF_K < CANDIDATE_DEPTH


def _record(chunk_id: int, document_id: int) -> RetrievedRecord:
    return RetrievedRecord(
        chunk_id=chunk_id,
        document_id=document_id,
        source_type="github",
        title=f"Commit {chunk_id}",
        content="body",
        url=f"https://github.com/o/r/commit/{chunk_id}",
        source_timestamp=None,
        authority=0.9,
        lexical_score=1.0,
        vector_score=0.5,
    )


def test_records_to_response_numbers_citations_from_one() -> None:
    evidence, citations = records_to_response([_record(11, 1), _record(22, 2)])

    assert [citation.id for citation in citations] == [1, 2]
    assert [item.citation_id for item in evidence] == [1, 2]
    assert [item.id for item in evidence] == ["chunk-11", "chunk-22"]


def test_records_to_response_on_no_records_emits_nothing() -> None:
    """A retrieval miss must not manufacture an empty citation for the answer to point at."""
    assert records_to_response([]) == ([], [])
