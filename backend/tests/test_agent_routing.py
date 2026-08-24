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


def test_issue_key_wins_over_incidental_commit_mention() -> None:
    """Was a strict xfail: "commit" used to be tested before the issue-key pattern.

    Routing is ordered by specificity now — a concrete identifier names one record, while a topic
    word names only a subject area.
    """
    assert classify_query("Which commits relate to ASK-6?") == "jira_issue_status"


def test_synthetic_evidence_is_scoped_to_the_demo_projects() -> None:
    evidence, citations = get_weekly_brief_evidence("project-atlas")
    assert evidence and citations


@pytest.mark.parametrize("project_id", ["askbase", "project-x", "unknown"])
def test_synthetic_evidence_is_never_lent_to_a_real_project(project_id: str) -> None:
    """The fabrication bug: this used to return Project Atlas fixtures for any project id."""
    assert is_synthetic_project(project_id) is False
    assert get_weekly_brief_evidence(project_id) == ([], [])


def test_demo_brief_is_scoped_to_synthetic_projects() -> None:
    """Regression: the canned Project Atlas brief leaked into real projects answered from the web.

    The web-fallback path sets `records` to empty while still holding evidence, and the synthesis
    branch keyed the demo brief on `not records` rather than on the project actually being a demo.
    """
    import inspect

    from app.agent import nodes

    source = inspect.getsource(nodes.synthesize)
    marker = "fallback_weekly_brief_answer()"
    assert marker in source
    guard = source.split(marker, 1)[1].split("\n)", 1)[0]
    assert "is_synthetic_project" in guard
