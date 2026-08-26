"""Follow-up detection and the identifier guard.

The gate is the risky half of multi-turn. Missing a follow-up costs an unhelpful answer; rewriting
a question that was already correct can move it onto the wrong answer path entirely, which is worse
than not having the feature.
"""

import json
from pathlib import Path

import pytest

from app.agent.followup import (
    extract_identifiers,
    introduces_unknown_identifier,
    is_self_contained,
    needs_resolution,
    resolve_followup,
)
from app.agent.routing import classify_query
from app.models.schemas import ConversationTurn

EVAL_DIR = Path(__file__).resolve().parents[1] / "evals"


def _live_eval_queries() -> list[str]:
    queries: list[str] = []
    for name in ("askbase.jsonl", "jira_askbase.jsonl"):
        for line in (EVAL_DIR / name).read_text().splitlines():
            if line.strip():
                queries.append(json.loads(line)["query"])
    return queries


@pytest.mark.parametrize("query", _live_eval_queries())
def test_no_live_eval_query_is_treated_as_a_follow_up(query: str) -> None:
    """The release gate sends no conversation id, so the gate must never fire on any of it.

    Asserted rather than assumed: a gate that fires here would send a deterministic exact-answer
    question through a model rewrite, and the 1.000 pass rate would start depending on the weather.
    """
    assert needs_resolution(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "Who is it assigned to?",
        "When was it last updated?",
        "What about the one before that?",
        "And the previous one?",
        "Why?",
        "Why not?",
        "What else?",
        "Who else worked on it?",
        "Is that ticket still open?",
        "Did they ship it?",
    ],
)
def test_back_references_need_resolution(query: str) -> None:
    assert needs_resolution(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "What is the status of ASK-6?",
        "What was the last commit by Manav0411?",
        "What blockers are open in AskBase?",
        "Which decisions were made about embeddings?",
        "What issues are assigned to Sarah Kim?",
        # No identifier, but no back-reference either: a normal standalone question.
        "What deployment work was done this week?",
        "Why was bcrypt pinned to version 4?",
        "When did the EC2 migration start?",
    ],
)
def test_self_contained_questions_are_left_alone(query: str) -> None:
    assert needs_resolution(query) is False


def test_assigned_to_without_a_name_is_not_self_contained() -> None:
    """The extractors decide, not substring matching.

    "Who is it assigned to?" contains the literal text "assigned to" but names nobody, so the Jira
    assignee extractor correctly returns None and the question really is a follow-up.
    """
    assert is_self_contained("What issues are assigned to Sarah Kim?") is True
    assert is_self_contained("Who is it assigned to?") is False


def test_this_week_is_not_a_back_reference() -> None:
    """Bare demonstratives are excluded precisely so this stays a normal question."""
    assert needs_resolution("What shipped this week?") is False


def test_empty_query_needs_nothing() -> None:
    assert needs_resolution("   ") is False


def test_resolution_is_what_changes_the_answer_path() -> None:
    """The whole phase in one assertion: the same intent, routed two different ways.

    Unresolved, a follow-up carries no identifier and falls through to generic retrieval. Resolved,
    it names a record and reaches the deterministic Jira tool.
    """
    assert classify_query("Who is it assigned to?") == "weekly_project_brief"
    assert classify_query("Who is ASK-6 assigned to?") == "jira_issue_status"


def test_identifier_extraction_covers_keys_and_hashes() -> None:
    found = extract_identifiers("ASK-6 was fixed by a1b2c3d and mentioned in ask-12")

    assert "ask-6" in found
    assert "ask-12" in found
    assert "a1b2c3d" in found


def test_guard_accepts_identifiers_carried_from_history() -> None:
    assert (
        introduces_unknown_identifier(
            "Who is ASK-6 assigned to?", ["What is the status of ASK-6?", "ASK-6 is In Review."]
        )
        is False
    )


def test_guard_rejects_an_invented_identifier() -> None:
    """Stops a confident, correctly cited answer about entirely the wrong ticket."""
    assert (
        introduces_unknown_identifier(
            "Who is ASK-7 assigned to?", ["What is the status of ASK-6?", "ASK-6 is In Review."]
        )
        is True
    )


class StubOllama:
    def __init__(self, payload: dict | Exception) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def generate_json(self, system_prompt, user_prompt, **kwargs) -> dict:
        self.prompts.append(user_prompt)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _history() -> list[ConversationTurn]:
    return [
        ConversationTurn(
            query="What is the status of ASK-6?",
            answer="ASK-6 is In Review [1].",
            retrieval_grade="correct",
        )
    ]


async def test_resolution_rewrites_against_history() -> None:
    client = StubOllama({"query": "Who is ASK-6 assigned to?"})

    resolved = await resolve_followup("Who is it assigned to?", _history(), client)

    assert resolved == "Who is ASK-6 assigned to?"
    assert "ASK-6 is In Review" in client.prompts[0]


async def test_resolution_returns_none_when_the_model_is_unavailable() -> None:
    """Ollama being down must degrade to the original question, never fail the request."""
    assert (
        await resolve_followup("Who is it assigned to?", _history(), StubOllama(RuntimeError()))
        is None
    )


async def test_resolution_rejects_a_rewrite_that_invents_a_ticket() -> None:
    client = StubOllama({"query": "Who is ASK-7 assigned to?"})

    assert await resolve_followup("Who is it assigned to?", _history(), client) is None


async def test_resolution_without_history_does_not_call_the_model() -> None:
    """The first turn of a conversation has nothing to resolve against."""
    client = StubOllama({"query": "should not be used"})

    assert await resolve_followup("Who is it assigned to?", [], client) is None
    assert client.prompts == []


async def test_an_unchanged_rewrite_is_treated_as_no_rewrite() -> None:
    client = StubOllama({"query": "Who is it assigned to?"})

    assert await resolve_followup("Who is it assigned to?", _history(), client) is None


async def test_chained_follow_ups_resolve_against_the_standalone_form() -> None:
    """Turn 3 must see turn 2's resolved question, not the pronoun that was typed."""
    history = _history() + [
        ConversationTurn(
            query="Who is it assigned to?",
            resolved_query="Who is ASK-6 assigned to?",
            answer="ASK-6 is assigned to Manav Goel [1].",
            retrieval_grade="correct",
        )
    ]
    client = StubOllama({"query": "When was ASK-6 last updated?"})

    resolved = await resolve_followup("When was it last updated?", history, client)

    assert resolved == "When was ASK-6 last updated?"
    assert "Who is ASK-6 assigned to?" in client.prompts[0]
    assert "Who is it assigned to?" not in client.prompts[0]
