"""The guardrail's job is to be cheap and conservative.

The two directions matter unequally, and the tests are weighted to match: admitting a greeting
wastes a few seconds, while rejecting a real question makes the product look broken with no
explanation the user can see. So the false-positive cases carry the most examples.
"""

import pytest

from app.agent.admission import is_small_talk


@pytest.mark.parametrize(
    "query",
    [
        "Hey",
        "hey",
        "HEY",
        "hey!",
        "hi",
        "hi there",
        "hello!!",
        "Hey there :)",
        "yo",
        "good morning",
        "good morning everyone",
        "thanks",
        "thank you",
        "thanks so much",
        "ok",
        "okay, thanks",
        "cool",
        "bye",
        "how are you",
        "how's it going",
        "what's up",
        "test",
        "testing",
        "ping",
        "",
        "   ",
        "?!",
        "\U0001f44b",
    ],
)
def test_rejects_pleasantries(query: str) -> None:
    assert is_small_talk(query) is True


@pytest.mark.parametrize(
    "query",
    [
        # The greeting-prefixed cases are the ones the check most easily gets wrong.
        "hey, what was the last commit?",
        "hi, what is the status of GW-3?",
        "thanks — can you check the blockers?",
        "ok so what changed in the last release?",
        "good morning, who is working on GW-13?",
        # Short inputs that are still questions.
        "GW-3",
        "commits?",
        "blockers",
        "why?",
        "status",
        # Ordinary questions.
        "What was the last conversation on slack?",
        "Are all the tasks complete?",
        "Why did we choose the grader model?",
        "What did we decide about pricing?",
        "test coverage",
        "is the pipeline done",
    ],
)
def test_admits_questions(query: str) -> None:
    assert is_small_talk(query) is False
