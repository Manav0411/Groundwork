"""How the retrieval report aggregates, which is not the same question as how retrieval ranks.

Both rules here replace something that could not fail. `negative_cases_returning_nothing` counted
negative cases that returned no evidence -- but `hybrid_retrieve` applies no relevance floor, so
any project with enough documents returns `limit` of them for every question however irrelevant.
That count was structurally 0 and was printed as a measurement in every retrieval baseline since
Phase 2.
"""

from evals.retrieval_models import CaseMetrics
from evals.retrieval_runner import summarise


def _case(
    case_id: str,
    category: str,
    *,
    recall: float,
    rr: float = 1.0,
    top_vector: float = 0.5,
    returned: int = 8,
) -> CaseMetrics:
    return CaseMetrics(
        case_id=case_id,
        category=category,
        query=f"query for {case_id}",
        retrieved=[],
        relevant=[],
        recall_at_k={8: recall},
        precision_at_k={8: recall},
        reciprocal_rank=rr,
        lexical_hits=0,
        returned=returned,
        top_vector_score=top_vector,
        min_vector_score=0.0,
        relevant_vector_scores=[],
    )


def test_negative_cases_do_not_drag_the_ranking_mean() -> None:
    """A negative case scores 0 recall by construction, so averaging it in measures the dataset's
    composition rather than the system's quality."""
    results = [
        _case("a", "single_document", recall=1.0),
        _case("b", "multi_document", recall=1.0),
        _case("n", "negative", recall=0.0),
    ]

    summary = summarise(results, dataset_name="d", ks=(8,), use_embeddings=True)

    assert summary.mean_recall_at_k[8] == 1.0, "negatives were averaged into the ranking metric"
    assert summary.scored_cases == 2
    assert summary.total_cases == 3
    assert summary.negative_cases == 1


def test_recall_and_mrr_share_one_denominator() -> None:
    """MRR always excluded negatives while recall did not, so the two were not comparable."""
    results = [
        _case("a", "single_document", recall=1.0, rr=1.0),
        _case("n", "negative", recall=0.0, rr=0.0),
    ]

    summary = summarise(results, dataset_name="d", ks=(8,), use_embeddings=True)

    assert summary.mean_recall_at_k[8] == summary.mrr == 1.0


def test_separation_is_positive_when_the_corpus_can_tell_them_apart() -> None:
    results = [
        _case("a", "single_document", recall=1.0, top_vector=0.60),
        _case("b", "multi_document", recall=1.0, top_vector=0.55),
        _case("n", "negative", recall=0.0, top_vector=0.30),
    ]

    summary = summarise(results, dataset_name="d", ks=(8,), use_embeddings=True)

    # Weakest in-corpus 0.55 against strongest out-of-corpus 0.30.
    assert summary.weakest_positive_top_score == 0.55
    assert summary.strongest_negative_top_score == 0.30
    assert round(summary.separation_margin, 3) == 0.25


def test_separation_goes_negative_when_the_distributions_overlap() -> None:
    """The failure the old count could never express: the best match for a question the corpus
    cannot answer outscores the worst match for one it can."""
    results = [
        _case("a", "single_document", recall=1.0, top_vector=0.31),
        _case("n", "negative", recall=0.0, top_vector=0.35),
    ]

    summary = summarise(results, dataset_name="d", ks=(8,), use_embeddings=True)

    assert summary.separation_margin < 0


def test_a_dataset_with_no_negatives_still_summarises() -> None:
    summary = summarise(
        [_case("a", "single_document", recall=1.0)],
        dataset_name="d",
        ks=(8,),
        use_embeddings=True,
    )

    assert summary.negative_cases == 0
    assert summary.mean_recall_at_k[8] == 1.0
