"""Routing and extraction for the Slack exact-answer path.

The database behaviour is covered in tests/integration/test_structured_db.py;
these are the parts that need no Postgres.
"""

import pytest

from app.agent.routing import classify_query, describe_route, is_structured
from app.services.structured_slack import extract_slack_channel


@pytest.mark.parametrize(
    "query",
    [
        "What was the last conversation on slack?",
        "What is the most recent thread in #groundwork-eng?",
        "newest slack discussion",
        "latest message in the deploys channel",
    ],
)
def test_recency_questions_route_to_slack(query: str) -> None:
    """The gap this path was built to close: these all used to reach retrieval
    and be refused, because semantic ranking has nothing to rank 'the last
    conversation' on."""
    assert classify_query(query) == "latest_slack_thread"
    assert is_structured("latest_slack_thread")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # Both halves are required.
        ("what happened on slack?", "weekly_project_brief"),
        ("what was the latest commit?", "latest_commit"),
        # A decision is content, and the newest thread is not reliably the one
        # that carries it -- the same reasoning that keeps commit-content
        # questions off the GitHub tool.
        ("What was the last decision made on slack?", "decision_history"),
        # An explicit identifier is more specific and must still win.
        ("what was the last update on GW-3?", "jira_issue_status"),
    ],
)
def test_questions_that_must_not_route_to_slack(query: str, expected: str) -> None:
    assert classify_query(query) == expected


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("newest thread in #ops", "ops"),
        ("#groundwork-eng latest thread", "groundwork-eng"),
        ("latest thread in channel ops", "ops"),
        ("most recent discussion in the deploys channel", "deploys"),
        # A bare noun after "in" is a topic, not a channel.
        ("last thread in production", None),
        # "slack" names the source, not a channel.
        ("what was the last conversation in slack?", None),
    ],
)
def test_channel_extraction(query: str, expected: str | None) -> None:
    assert extract_slack_channel(query) == expected


def test_route_description_names_the_scope() -> None:
    """The trace has to say why it chose this path, and over what."""
    assert "#ops" in describe_route("latest_slack_thread", "newest thread in #ops")
    assert "indexed channels" in describe_route(
        "latest_slack_thread", "what was the last conversation on slack?"
    )
