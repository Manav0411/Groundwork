import pytest

from app.agent.graph import classify_query
from app.connectors.synthetic_workspace import (
    get_weekly_brief_evidence,
    is_synthetic_project,
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What was the last commit by Raghav?", "latest_commit"),
        ("What is the status of ASK-6?", "jira_issue_status"),
        ("what is the status of ask-6?", "jira_issue_status"),
        ("Which issues are assigned to Manav?", "jira_assignee"),
        ("What blockers are open?", "blocker_investigation"),
        ("What decisions were made about the migration?", "decision_history"),
        ("Generate a weekly brief.", "weekly_project_brief"),
    ],
)
def test_classify_query_routes_intents(query: str, expected: str) -> None:
    assert classify_query(query) == expected


@pytest.mark.xfail(
    reason="'commit' is tested before the issue-key pattern; the LangGraph planner in Phase 3 "
    "replaces this ordering-sensitive router.",
    strict=True,
)
def test_issue_key_wins_over_incidental_commit_mention() -> None:
    assert classify_query("Which commits relate to ASK-6?") == "jira_issue_status"


def test_synthetic_evidence_is_scoped_to_the_demo_projects() -> None:
    evidence, citations = get_weekly_brief_evidence("project-atlas")
    assert evidence and citations


@pytest.mark.parametrize("project_id", ["askbase", "project-x", "unknown"])
def test_synthetic_evidence_is_never_lent_to_a_real_project(project_id: str) -> None:
    """The fabrication bug: this used to return Project Atlas fixtures for any project id."""
    assert is_synthetic_project(project_id) is False
    assert get_weekly_brief_evidence(project_id) == ([], [])
