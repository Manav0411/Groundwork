from app.models.schemas import Citation
from app.services.citations import validate_citations


def _citation(ordinal: int) -> Citation:
    return Citation(id=ordinal, source_type="github", title=f"Commit {ordinal}")


def test_valid_markers_pass_through_unchanged() -> None:
    answer = "The payment gateway is blocked [1] and the backfill job failed [2]."
    result = validate_citations(answer, [_citation(1), _citation(2)])

    assert result.answer == answer
    assert result.valid_ordinals == [1, 2]
    assert result.invalid_ordinals == []
    assert result.grade_override is None
    assert result.gaps == []


def test_invented_marker_is_stripped_and_downgrades_the_grade() -> None:
    answer = "The gateway is blocked [1] and the vendor confirmed a fix [7]."
    result = validate_citations(answer, [_citation(1)])

    assert "[7]" not in result.answer
    assert "[1]" in result.answer
    assert result.invalid_ordinals == [7]
    assert result.grade_override == "ambiguous"
    assert len(result.gaps) == 1
    assert "[7]" in result.gaps[0]


def test_stripping_a_marker_does_not_leave_dangling_whitespace() -> None:
    result = validate_citations("The build is green [4].", [_citation(1)])
    assert result.answer == "The build is green."


def test_answer_that_cites_nothing_despite_evidence_is_downgraded() -> None:
    result = validate_citations("The gateway is blocked.", [_citation(1)])

    assert result.uncited is True
    assert result.grade_override == "ambiguous"
    assert result.gaps


def test_answer_with_no_evidence_to_cite_is_not_penalised() -> None:
    """A not-found disclosure is correct to cite nothing."""
    result = validate_citations("No indexed commit was found for 'nobody'.", [])

    assert result.uncited is False
    assert result.grade_override is None
    assert result.gaps == []
