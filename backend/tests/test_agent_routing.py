import pytest

from app.agent.graph import classify_query
from app.agent.routing import is_structured
from app.connectors import synthetic_workspace
from app.services.structured_github import extract_commit_author, extract_commit_sha


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


def test_the_sample_projects_carry_no_evidence() -> None:
    """The fabrication surface is gone, not merely scoped.

    This module used to hold invented Project Atlas blockers and sprint plans, returned by
    `get_weekly_brief_evidence` for any project id. Scoping it to the demo projects contained the
    leak but kept the mechanism, so a later change could reopen it. There is now nothing to leak:
    the sample projects are empty shells and take the ordinary no-evidence path.
    """
    assert not hasattr(synthetic_workspace, "get_weekly_brief_evidence")
    assert all(
        not hasattr(synthetic_workspace, name)
        for name in ("EVIDENCE", "CITATIONS", "is_synthetic_project")
    )
    assert [project.id for project in synthetic_workspace.get_projects()] == [
        "project-atlas",
        "project-orion",
    ]


def test_synthesis_has_no_canned_answer() -> None:
    """Regression: a hardcoded brief carrying [1]-[5] markers used to be returned verbatim.

    It was guarded three ways and unreachable in normal operation, which is exactly why it
    survived so long. The only fallback now restates retrieved evidence.
    """
    import inspect

    from app.agent import nodes
    from app.services import llm

    assert not hasattr(llm, "fallback_weekly_brief_answer")
    source = inspect.getsource(nodes.synthesize)
    assert "fallback_answer_from_evidence" in source
    assert "weekly_brief" not in source
    assert "synthetic" not in source.casefold()


def test_a_named_commit_hash_gets_its_own_exact_lookup() -> None:
    """A question naming one record has an exact answer, so it must not be summarized.

    This routed to retrieval first, as a quick fix for "what features did the commit f4a941f
    change?" being told an author was required. Retrieval was the wrong destination: it found the
    right commit and the 3B model then answered "I couldn't find any information about commit
    f4a941f" — contradicting the evidence in position one. It is a structured path now.
    """
    assert classify_query("What was commit f4a941f about?") == "commit_detail"
    assert classify_query("What features did the commit f4a941f change?") == "commit_detail"
    assert is_structured("commit_detail") is True


def test_a_commit_question_without_a_hash_still_reaches_the_structured_tool() -> None:
    """Guards the live eval cases that expect "I need an author name"."""
    assert classify_query("What was the latest commit?") == "latest_commit"
    assert classify_query("Show me the most recent commit in AskBase") == "latest_commit"


def test_hex_looking_words_are_not_mistaken_for_commit_hashes() -> None:
    """"defaced" is seven hex characters; requiring a digit is what separates it from a SHA."""
    assert extract_commit_sha("the commit defaced the layout") is None
    assert extract_commit_sha("commit f4a941f") == "f4a941f"


def test_author_extraction_stops_at_a_positional_clause() -> None:
    """"by Manav0411 before 4121d76?" once captured the whole tail as the author name."""
    assert extract_commit_author("What was the commit by Manav0411 before 4121d76?") == "Manav0411"
    assert extract_commit_author("the commit by Sarah Kim after abc1234") == "Sarah Kim"
    # The clauses that already worked must keep working.
    assert extract_commit_author("last commit by Manav0411 on project AskBase?") == "Manav0411"
    assert extract_commit_author("What was the last commit by Manav Goel?") == "Manav Goel"


def test_a_commit_question_describing_content_goes_to_retrieval() -> None:
    """The structured tool answers one question: which commit is Nth-newest for an author.

    "Which commit dropped the HuggingFace dependency?" names no author, no hash, and no position,
    so it reached that tool and was told an author was required — for a question no author would
    have answered. The golden conversation suite found this on a first turn, where no amount of
    follow-up resolution could have helped.
    """
    assert classify_query("Which commit dropped the HuggingFace dependency?") != "latest_commit"
    assert classify_query("Which commit introduced the retry logic?") != "latest_commit"


def test_superlative_commit_questions_still_reach_the_structured_tool() -> None:
    """Guards the three live eval cases that expect "I need an author name"."""
    for query in (
        "What was the latest commit?",
        "Show me the most recent commit in AskBase",
        "What is the latest commit on project AskBase?",
    ):
        assert classify_query(query) == "latest_commit", query


@pytest.mark.parametrize(
    "query",
    [
        "Are all the tasks complete?",
        "How many Jira issues are still open?",
        "Is every ticket done?",
        "How many stories are outstanding?",
    ],
)
def test_quantifier_questions_reach_the_counting_tool(query: str) -> None:
    """These had no answer before: the grader rejects every chunk, correctly.

    Sufficiency is judged per passage, and a quantifier is answered by the set — no single chunk
    states that all the work is done. Counting rows answers it exactly instead, so the question
    belongs on the deterministic path rather than in retrieval.
    """
    assert classify_query(query) == "jira_project_status"
    assert is_structured("jira_project_status")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        # A work noun with no quantifier is an ordinary topic question.
        ("What is the status of ASK-6?", "jira_issue_status"),
        ("Which issues are assigned to Manav?", "jira_assignee"),
        # Blockers keep their own route; "open" alone must not capture them.
        ("What blockers are open in AskBase?", "blocker_investigation"),
        # A quantifier with no work noun is too vague to claim.
        ("What work was done on the Slack connector?", "weekly_project_brief"),
        ("Is everything done?", "weekly_project_brief"),
    ],
)
def test_counting_route_does_not_swallow_neighbouring_intents(query: str, expected: str) -> None:
    assert classify_query(query) == expected
