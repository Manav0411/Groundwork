from datetime import UTC, datetime
from pathlib import Path

from app.models.schemas import Citation, EvidenceItem, QueryResponse, TraceStep
from evals.deterministic import evaluate_response
from evals.models import EvaluationCase
from evals.runner import load_cases, render_markdown

TRACE = [
    TraceStep(
        name=name,
        status="completed",
        duration_ms=0,
        summary="ok",
    )
    for name in (
        "Input Guardrail",
        "Planner",
        "Structured GitHub Query",
        "Citation Validator",
    )
]
SHA = "f4a941f777055b47be553f28115dac1fa5018d93"


def found_case() -> EvaluationCase:
    return EvaluationCase(
        id="found",
        category="exact_answer",
        query="What was the last commit by Manav0411?",
        expected_outcome="found",
        expected_grade="correct",
        expected_citations=1,
        expected_sha=SHA,
        expected_author="Manav Goel",
        expected_title="Refactor README for better structure and clarity",
    )


def test_deterministic_evaluator_accepts_exact_cited_commit() -> None:
    response = QueryResponse(
        conversation_id="conv-eval",
        answer=(
            "The latest indexed commit by Manav Goel is `f4a941f` — "
            "Refactor README for better structure and clarity [1]."
        ),
        retrieval_grade="correct",
        tools_used=["planner", "structured_github_query"],
        citations=[
            Citation(
                id=1,
                source_type="github",
                title="Refactor README for better structure and clarity",
                url=f"https://github.com/Manav0411/AskBase/commit/{SHA}",
                timestamp=datetime.now(UTC).isoformat(),
            )
        ],
        evidence=[
            EvidenceItem(
                id="chunk-1",
                source_type="github",
                title="Refactor README for better structure and clarity",
                snippet="Commit evidence",
                citation_id=1,
                authority=0.95,
            )
        ],
        unresolved_gaps=[],
        trace=TRACE,
    )

    checks = evaluate_response(found_case(), response, duration_ms=25)

    assert checks
    assert all(check.passed for check in checks)


def test_deterministic_evaluator_rejects_wrong_sha_and_unsafe_citation() -> None:
    response = QueryResponse(
        conversation_id="conv-eval",
        answer="The latest commit is `0000000` [1].",
        retrieval_grade="correct",
        tools_used=["planner", "structured_github_query"],
        citations=[
            Citation(
                id=1,
                source_type="github",
                title="Wrong commit",
                url="http://example.com/commit/not-a-sha",
            )
        ],
        evidence=[
            EvidenceItem(
                id="chunk-1",
                source_type="github",
                title="Wrong commit",
                snippet="Wrong evidence",
                citation_id=1,
                authority=0.95,
            )
        ],
        unresolved_gaps=[],
        trace=TRACE,
    )

    failed = {
        check.name
        for check in evaluate_response(found_case(), response, duration_ms=25)
        if not check.passed
    }

    assert {"sha_in_answer", "citation_sha", "citation_url_shape", "author_identity"} <= failed


def test_non_found_case_requires_gap_and_no_evidence() -> None:
    case = EvaluationCase(
        id="unknown",
        category="failure_disclosure",
        query="What was the last commit by Nobody?",
        expected_outcome="not_found",
        expected_grade="ambiguous",
        expected_citations=0,
        answer_contains=["No indexed GitHub commit was found"],
        expect_unresolved_gap=True,
    )
    response = QueryResponse(
        conversation_id="conv-eval",
        answer="No indexed GitHub commit was found for 'Nobody'.",
        retrieval_grade="ambiguous",
        tools_used=["planner", "structured_github_query"],
        citations=[],
        evidence=[],
        unresolved_gaps=["No matching commit exists."],
        trace=TRACE,
    )

    assert all(check.passed for check in evaluate_response(case, response, duration_ms=10))


def test_askbase_dataset_is_valid_and_unique() -> None:
    dataset = Path(__file__).parents[1] / "evals" / "askbase.jsonl"
    cases = load_cases(dataset)

    assert len(cases) == 16
    assert len({case.id for case in cases}) == len(cases)
    assert {case.category for case in cases} >= {
        "exact_answer",
        "failure_disclosure",
        "input_guardrail",
        "project_isolation",
    }


def test_jira_dataset_is_valid_and_unique() -> None:
    dataset = Path(__file__).parents[1] / "evals" / "jira_askbase.jsonl"
    cases = load_cases(dataset)

    assert len(cases) == 5
    assert all(case.source_type == "jira" for case in cases)
    assert len({case.id for case in cases}) == len(cases)


def test_markdown_report_lists_failed_checks() -> None:
    from evals.models import CaseResult, CheckResult, EvaluationSummary

    summary = EvaluationSummary(
        dataset="sample.jsonl",
        started_at="2026-08-15T00:00:00Z",
        completed_at="2026-08-15T00:00:01Z",
        total_cases=1,
        passed_cases=0,
        pass_rate=0,
        mean_score=0.5,
        mean_latency_ms=10,
        p95_latency_ms=10,
        results=[
            CaseResult(
                case_id="broken",
                category="contract",
                passed=False,
                score=0.5,
                duration_ms=10,
                checks=[CheckResult(name="sha", passed=False, detail="wrong sha")],
                answer="wrong",
            )
        ],
    )

    report = render_markdown(summary)

    assert "`broken`" in report
    assert "`sha`: wrong sha" in report
