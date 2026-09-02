"""The graph's shape is a contract, so assert it rather than trusting it stayed wired correctly."""

import pytest

from app.agent.graph import AGENT_GRAPH, _route_after_grade, _route_after_plan
from app.core.config import settings
from app.services.grading import GradeResult
from app.services.retrieval import RetrievedRecord


def _edges() -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in AGENT_GRAPH.get_graph().edges}


def _record() -> RetrievedRecord:
    return RetrievedRecord(
        chunk_id=1,
        document_id=1,
        source_type="github",
        title="t",
        content="c",
        url=None,
        source_timestamp=None,
        authority=0.9,
        lexical_score=1.0,
        vector_score=0.5,
    )


def test_every_path_ends_at_citation_validation() -> None:
    """No route may reach the end without its citations being checked."""
    edges = _edges()
    for terminal in ("structured_github", "structured_jira", "structured_slack", "synthesize"):
        assert (terminal, "validate") in edges
    assert any(source == "validate" for source, _ in edges)


def test_exact_answer_paths_skip_grading_and_synthesis() -> None:
    """This is what keeps them deterministic and independent of any model."""
    edges = _edges()
    for path in ("structured_github", "structured_jira", "structured_slack"):
        assert (path, "grade") not in edges
        assert (path, "synthesize") not in edges


def test_resolution_runs_before_routing() -> None:
    """Routing reads identifiers out of the question text, so a follow-up must be resolved first.

    If `plan` ever precedes `resolve` again, "who is it assigned to?" goes back to being routed on
    a pronoun and answered by generic retrieval.
    """
    edges = _edges()
    assert ("guardrail", "resolve") in edges
    assert ("resolve", "plan") in edges
    assert ("guardrail", "plan") not in edges


def test_corrective_cycle_exists() -> None:
    """The one real cycle, and the reason a graph is warranted at all."""
    edges = _edges()
    assert ("grade", "correct") in edges
    assert ("correct", "grade") in edges


@pytest.mark.parametrize(
    ("query_type", "jira_configured", "expected"),
    [
        ("latest_commit", False, "structured_github"),
        ("jira_issue_status", False, "structured_jira"),
        ("jira_assignee", False, "structured_jira"),
        ("latest_slack_thread", False, "structured_slack"),
        ("blocker_investigation", True, "structured_jira"),
        # Without Jira configured, a blocker question is just a retrieval question.
        ("blocker_investigation", False, "retrieve"),
        ("decision_history", True, "retrieve"),
        ("weekly_project_brief", False, "retrieve"),
    ],
)
def test_plan_routing(query_type: str, jira_configured: bool, expected: str) -> None:
    state = {"query_type": query_type, "jira_configured": jira_configured}
    assert _route_after_plan(state) == expected  # type: ignore[arg-type]


def test_sufficient_evidence_skips_correction() -> None:
    state = {"grade_result": GradeResult(grade="correct", kept=[_record()])}
    assert _route_after_grade(state) == "settle_evidence"  # type: ignore[arg-type]


def test_insufficient_evidence_triggers_correction_within_budget() -> None:
    state = {
        "grade_result": GradeResult(grade="incorrect", kept=[]),
        "session": object(),
        "project_exists": True,
        "attempt": 0,
    }
    assert _route_after_grade(state) == "correct"  # type: ignore[arg-type]


def test_correction_budget_is_bounded() -> None:
    """Exhausting the budget must stop correcting, or the cycle never terminates."""
    state = {
        "grade_result": GradeResult(grade="incorrect", kept=[]),
        "session": object(),
        "project_exists": True,
        "attempt": settings.corrective_max_attempts,
    }
    assert _route_after_grade(state) != "correct"  # type: ignore[arg-type]


def test_web_fallback_is_skipped_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tavily_api_key", None, raising=False)
    state = {
        "grade_result": GradeResult(grade="incorrect", kept=[]),
        "session": object(),
        "project_exists": True,
        "attempt": settings.corrective_max_attempts,
    }
    assert _route_after_grade(state) == "settle_evidence"  # type: ignore[arg-type]


def test_web_fallback_runs_once_correction_is_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tavily_api_key", "tvly-test", raising=False)
    state = {
        "grade_result": GradeResult(grade="incorrect", kept=[]),
        "session": object(),
        "project_exists": True,
        "attempt": settings.corrective_max_attempts,
    }
    assert _route_after_grade(state) == "web_fallback"  # type: ignore[arg-type]
