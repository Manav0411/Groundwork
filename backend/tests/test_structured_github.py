import pytest

from app.services.structured_github import extract_commit_author, normalize_author_identity
from app.services.structured_jira import extract_assignee, extract_issue_key


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What was the last commit by Raghav on Project X?", "Raghav"),
        ("latest commit by Raghav Sharma for project atlas", "Raghav Sharma"),
        ("Show the last commit by raghav@example.com.", "raghav@example.com"),
        ("What was the latest commit?", None),
    ],
)
def test_extract_commit_author(query: str, expected: str | None) -> None:
    assert extract_commit_author(query) == expected


def test_normalize_author_identity_is_case_insensitive() -> None:
    assert normalize_author_identity("  RAGHAV   Sharma ") == "raghav sharma"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What is the status of ASK-6?", "ASK-6"),
        ("tell me about ask-12", "ASK-12"),
        ("What blockers are open?", None),
    ],
)
def test_extract_jira_issue_key(query: str, expected: str | None) -> None:
    assert extract_issue_key(query) == expected


def test_extract_jira_assignee() -> None:
    assert extract_assignee("Which issues are assigned to Manav Goel?") == "Manav Goel"
