"""What the answer does not cite is not presented as a citation.

Live testing showed a 16-chunk retrieval rendered as 16 citation chips beneath an answer that
referenced none of them, with the validator's own gap saying so. For a product whose claim is cited
evidence, showing support the answer never asserted is the wrong direction to fail in.
"""

import pytest

from app.agent import nodes
from app.agent.tracing import TraceRecorder
from app.models.schemas import Citation, EvidenceItem


def _citation(ordinal: int) -> Citation:
    return Citation(id=ordinal, source_type="slack", title=f"thread {ordinal}", url=None)


def _evidence(ordinal: int) -> EvidenceItem:
    return EvidenceItem(
        id=f"chunk-{ordinal}",
        source_type="slack",
        title=f"thread {ordinal}",
        snippet="...",
        citation_id=ordinal,
        authority=0.8,
    )


def _state(answer: str, count: int) -> dict:
    return {
        "answer": answer,
        "citations": [_citation(index) for index in range(1, count + 1)],
        "evidence": [_evidence(index) for index in range(1, count + 1)],
        "retrieval_grade": "correct",
        "unresolved_gaps": [],
        "trace": TraceRecorder(),
    }


async def test_only_the_cited_evidence_is_emitted() -> None:
    """Eight chunks retrieved, one referenced: one citation reaches the answer."""
    result = await nodes.validate(_state("The thread settled on Cohere [5].", 8))

    assert [citation.id for citation in result["citations"]] == [5]
    assert [item.citation_id for item in result["evidence"]] == [5]


async def test_an_answer_citing_nothing_emits_no_citations() -> None:
    """The screenshot case: 16 chips under an answer that referenced none of them."""
    result = await nodes.validate(_state("I'm not aware of any specific project.", 16))

    assert result["citations"] == []
    assert result["evidence"] == []
    assert result["retrieval_grade"] == "ambiguous"
    assert any("did not cite any" in gap for gap in result["unresolved_gaps"])


async def test_every_cited_marker_is_kept() -> None:
    result = await nodes.validate(_state("Both [1] and [3] agree.", 3))

    assert [citation.id for citation in result["citations"]] == [1, 3]
    assert result["retrieval_grade"] == "correct"


async def test_an_unsupported_marker_is_stripped_and_not_emitted() -> None:
    result = await nodes.validate(_state("Cited [1] and also [9].", 2))

    assert "[9]" not in result["answer"]
    assert [citation.id for citation in result["citations"]] == [1]
    assert result["retrieval_grade"] == "ambiguous"


async def test_an_answer_with_no_evidence_at_all_is_untouched() -> None:
    """A not-found disclosure is correct to cite nothing and must not be downgraded for it."""
    state = _state("No indexed evidence matched this question.", 0)

    result = await nodes.validate(state)

    assert result["citations"] == []
    assert result["retrieval_grade"] == "correct"
    assert result["unresolved_gaps"] == []


@pytest.mark.parametrize("count", [1, 5, 20])
async def test_the_trace_reports_what_was_dropped(count: int) -> None:
    state = _state("Answer with no markers.", count)

    await nodes.validate(state)

    summary = state["trace"].steps[-1].summary
    assert f"Dropped {count}" in summary
