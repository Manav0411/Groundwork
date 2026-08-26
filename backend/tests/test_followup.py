"""Follow-up detection and the identifier guard.

The gate is the risky half of multi-turn. Missing a follow-up costs an unhelpful answer; rewriting
a question that was already correct can move it onto the wrong answer path entirely, which is worse
than not having the feature.
"""

import json
from pathlib import Path

import pytest

from app.agent.followup import (
    carry_forward_author,
    extract_identifiers,
    introduces_unknown_identifier,
    is_self_contained,
    is_underspecified,
    needs_resolution,
    rebuild_positional_question,
    resolve_followup,
    restates_an_earlier_turn,
)
from app.agent.routing import classify_query
from app.models.schemas import ConversationTurn
from app.services.structured_github import extract_commit_author, extract_commit_offset

EVAL_DIR = Path(__file__).resolve().parents[1] / "evals"


def _live_eval_queries() -> list[str]:
    queries: list[str] = []
    for name in ("askbase.jsonl", "jira_askbase.jsonl"):
        for line in (EVAL_DIR / name).read_text().splitlines():
            if line.strip():
                queries.append(json.loads(line)["query"])
    return queries


@pytest.mark.parametrize("query", _live_eval_queries())
async def test_no_live_eval_query_is_ever_rewritten(query: str) -> None:
    """The release gate sends no conversation id, so nothing in it may reach a model rewrite.

    Asserted rather than assumed: a rewrite here would put a deterministic exact-answer question
    through a model, and the 1.000 pass rate would start depending on the weather.

    The guarantee is empty history, not the gate. Three eval cases — "What was the latest commit?"
    and friends — legitimately *are* underspecified, and inside a conversation they should resolve
    against whoever was being discussed. Standing alone they must instead reach the tool and be
    told an author is required, which is what those cases assert.
    """
    client = StubOllama({"query": "a rewrite that must never be produced"})

    assert await resolve_followup(query, [], client) is None
    assert client.prompts == [], "The model must not be consulted without history."


@pytest.mark.parametrize("query", [q for q in _live_eval_queries() if "by " in q or "ASK-" in q])
def test_eval_queries_naming_a_record_are_never_follow_ups(query: str) -> None:
    """A question naming its own record is self-contained even mid-conversation."""
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


@pytest.mark.parametrize(
    "query",
    [
        "what features did last commit changed?",
        "What was the latest commit?",
        "Show me the most recent commit",
    ],
)
def test_a_commit_question_naming_no_author_is_underspecified(query: str) -> None:
    """Routable is not answerable.

    These reach the GitHub tool on the word "commit" and are then told an author is required.
    Standing alone that is correct. In a conversation that already named whose commits are under
    discussion, refusing instead of using what was just said is the bug live testing exposed.
    """
    assert is_underspecified(query) is True
    assert needs_resolution(query) is True


def test_a_commit_question_naming_its_author_is_not_underspecified() -> None:
    assert is_underspecified("What was the last commit by Manav0411?") is False
    assert needs_resolution("What was the last commit by Manav0411?") is False


def test_a_broad_decision_question_is_not_underspecified() -> None:
    """Decision and blocker questions take retrieval, which handles a broad question fine."""
    assert is_underspecified("Which decisions were made about embeddings?") is False
    assert is_underspecified("What blockers are open?") is False


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


async def test_resolution_rejects_a_rewrite_that_echoes_an_earlier_question() -> None:
    """Live testing caught this: "What was it about?" came back as the previous question verbatim.

    It passes every other check — it differs from the follow-up, and it introduces no new
    identifier — while throwing away what was actually asked. The agent then confidently
    re-answered the previous turn as though it were new.
    """
    client = StubOllama({"query": "What is the status of ASK-6?"})

    assert await resolve_followup("What was it about?", _history(), client) is None


def test_echo_detection_ignores_casing_and_punctuation() -> None:
    assert restates_an_earlier_turn("what is the status of ask-6", _history()) is True
    assert restates_an_earlier_turn("What was ASK-6 about?", _history()) is False


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


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What was the last commit by X?", 0),
        ("What was the latest commit?", 0),
        ("What was the second-to-last commit?", 1),
        ("What was the 2nd latest commit?", 1),
        ("the commit before that", 1),
        ("the previous commit", 1),
        # A bare "before" counts only when a hash follows it.
        ("What was the commit by X before 4121d76?", 1),
        ("What changed before the release?", 0),
        ("third-to-last commit", 2),
        ("What was the 5th most recent commit?", 4),
    ],
)
def test_commit_position_is_read_from_the_question(query: str, expected: int) -> None:
    """Position is part of the question, so it has to be part of the query.

    Before this, "second-to-last commit by X" ran the same lookup as "last commit by X" and
    returned the newest one — a confidently wrong answer nothing distinguished from a right one.
    """
    assert extract_commit_offset(query) == expected


def test_a_dropped_author_is_carried_forward_from_the_conversation() -> None:
    """The model resolves the ordinal and loses the person; this puts the person back.

    Deterministic by construction: it copies an author string that literally appeared in an earlier
    question, so it cannot introduce someone the conversation never mentioned.
    """
    history = [
        ConversationTurn(
            query="What was the last commit by Manav0411?",
            answer="The latest indexed commit by Manav Goel is `f4a941f`.",
            retrieval_grade="correct",
        )
    ]

    carried = carry_forward_author("What was the second-to-last commit?", history)

    assert carried == "What was the second-to-last commit by Manav0411?"


def test_carry_forward_never_overrides_an_author_already_named() -> None:
    history = [
        ConversationTurn(
            query="What was the last commit by Manav0411?",
            answer="...",
            retrieval_grade="correct",
        )
    ]

    assert carry_forward_author("What was the last commit by Sarah Kim?", history) is None


def test_carry_forward_ignores_questions_that_are_not_about_commits() -> None:
    history = [
        ConversationTurn(
            query="What was the last commit by Manav0411?",
            answer="...",
            retrieval_grade="correct",
        )
    ]

    assert carry_forward_author("What is the status of ASK-6?", history) is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What was the 50th commit by Manav0411?", 49),
        ("show me the 3rd latest one", 2),
        ("what did the previous one do?", 1),
        ("the 10th most recent commit", 9),
        ("the sixth latest commit", 5),
        ("the first commit", 0),
    ],
)
def test_numeric_and_extended_ordinals_are_read(query: str, expected: int) -> None:
    """ "the 50th commit" was read as offset 0 and answered with the newest commit.

    Every field of that answer was well-formed. Only the position was wrong, which is the failure
    mode this whole path exists to prevent, so the word list is not enough on its own.
    """
    assert extract_commit_offset(query) == expected


@pytest.mark.parametrize(
    "query",
    ["show me the 3rd latest one", "what did the previous one do?", "the 50th one"],
)
def test_a_positional_question_without_the_word_commit_still_needs_resolution(query: str) -> None:
    """These name no record and never say "commit", so nothing else in the gate caught them."""
    assert needs_resolution(query) is True


def test_a_positional_question_is_rebuilt_without_the_model() -> None:
    """Position and author are both recoverable from the question and the conversation.

    The model loses each of them readily — "show me the 3rd latest one" came back as
    "What was commit f4a941f about?", dropping the position and confidently answering about the
    newest commit. Reconstructing directly is exact and skips the inference call entirely.
    """
    history = [
        ConversationTurn(
            query="What was the last commit by Manav0411?",
            answer="The latest indexed commit by Manav Goel is `f4a941f`.",
            retrieval_grade="correct",
        )
    ]

    rebuilt = rebuild_positional_question("show me the 3rd latest one", history)

    assert rebuilt == "What was the third most recent commit by Manav0411?"
    # The rebuilt form must round-trip back through the extractors it will be routed by.
    assert extract_commit_offset(rebuilt) == 2
    assert classify_query(rebuilt) == "latest_commit"


def test_rebuild_declines_when_the_question_is_not_positional() -> None:
    history = [
        ConversationTurn(
            query="What was the last commit by Manav0411?", answer="...", retrieval_grade="correct"
        )
    ]

    assert rebuild_positional_question("what was it about?", history) is None


def test_rebuild_declines_when_no_author_was_ever_named() -> None:
    """Without an author there is nothing deterministic to rebuild from; the model gets it."""
    history = [
        ConversationTurn(
            query="What blockers are open?", answer="ASK-6 is blocked.", retrieval_grade="correct"
        )
    ]

    assert rebuild_positional_question("show me the 3rd latest one", history) is None


def test_absolute_and_relative_positions_are_not_conflated() -> None:
    """Conflating them walks the wrong way through history.

    "the 3rd latest one" counts from the newest commit. "the one before that" counts from whichever
    commit the conversation last named — which is what makes asking it repeatedly step backwards
    one commit at a time instead of returning to the same one.
    """
    first = ConversationTurn(
        query="What was the last commit by Manav0411?",
        answer="The latest indexed commit by Manav Goel is `f4a941f` - Refactor README.",
        retrieval_grade="correct",
    )
    second = ConversationTurn(
        query="And what was last second commit?",
        resolved_query="What was the second most recent commit by Manav0411?",
        answer="The second most recent indexed commit is `4121d76` - Remove code.",
        retrieval_grade="correct",
    )

    assert (
        rebuild_positional_question("show me the 3rd latest one", [first])
        == "What was the third most recent commit by Manav0411?"
    )
    assert (
        rebuild_positional_question("and the one before that?", [first])
        == "What was the commit by Manav0411 before f4a941f?"
    )
    # Asked again a turn later, it must anchor on the newer answer and step back once more.
    assert (
        rebuild_positional_question("and the one before that?", [first, second])
        == "What was the commit by Manav0411 before 4121d76?"
    )


def test_relative_position_falls_back_to_absolute_without_a_hash() -> None:
    """Nothing to anchor on means counting from the newest, which is the best available reading."""
    history = [
        ConversationTurn(
            query="What was the last commit by Manav0411?",
            answer="No hash in this answer.",
            retrieval_grade="correct",
        )
    ]

    assert (
        rebuild_positional_question("the previous one", history)
        == "What was the second most recent commit by Manav0411?"
    )


def test_a_dangling_demonstrative_is_a_follow_up_even_when_a_person_is_named() -> None:
    """Naming one record does not resolve a different dangling reference.

    "What was the reply by Manav on that?" names Manav and still leaves "that" pointing at whatever
    the previous turn was about. Checking `names_a_record` first made the whole question look
    self-contained, so resolution never ran.
    """
    assert needs_resolution("What was the reply by Manav on that ?") is True
    assert needs_resolution("What did they decide about that?") is True


def test_a_demonstrative_with_a_noun_after_it_is_not_dangling() -> None:
    """ "this week" and "that ticket" are ordinary phrases, not back-references on their own."""
    assert needs_resolution("What shipped this week?") is False
    assert needs_resolution("What was the last commit by Manav0411?") is False


def test_author_extraction_stops_at_any_prepositional_clause() -> None:
    """Restricting the stop to "on project|repo" captured "Manav on that" as an author name."""
    assert extract_commit_author("What was the reply by Manav on that ?") == "Manav"
    assert extract_commit_author("commit by Sarah Kim about the retry logic") == "Sarah Kim"
    # The repository clauses the eval set depends on must keep working.
    assert extract_commit_author("last commit by Manav0411 on project AskBase?") == "Manav0411"
    assert extract_commit_author("commit by Manav0411 for repository AskBase.") == "Manav0411"
    assert extract_commit_author("What was the last commit by Manav Goel?") == "Manav Goel"


async def test_a_rewrite_containing_a_citation_marker_is_rejected() -> None:
    """A question never carries a citation marker; one means the answer was pasted in.

    Live testing produced: 'What features did the commit f4a941f by Manav Goel — "Refactor
    README...", committed at 2026-05-11T14:38:59+00:00 [1] change?'. It routed correctly by
    accident and was still shown to the user as the question they had asked.
    """
    client = StubOllama({"query": "What did commit f4a941f — “Refactor README” [1] change?"})

    assert await resolve_followup("what did it change?", _history(), client) is None


def test_a_demonstrative_followed_by_a_verb_is_a_back_reference() -> None:
    """ "which channel was that discussed in?" points backwards; "that ticket" does not.

    Found by the golden conversation suite: the turn never resolved, because "that" was neither at
    the end of the clause nor followed by a known noun.
    """
    assert needs_resolution("which channel was that discussed in?") is True
    assert needs_resolution("when was that decided?") is True
    assert needs_resolution("What shipped this week?") is False


def test_a_content_commit_question_is_not_underspecified() -> None:
    """It is answered by retrieval, so there is nothing for a conversation to fill in."""
    assert is_underspecified("Which commit dropped the HuggingFace dependency?") is False
    assert is_underspecified("What was the latest commit?") is True


def test_the_history_prompt_marks_the_most_recent_exchange() -> None:
    """A pronoun almost always refers to the newest turn, and an unlabelled transcript hides that.

    Found by the golden conversation suite: after six Jira questions, "who is it assigned to?"
    resolved against ASK-5 — the oldest turn still inside the five-turn window — instead of ASK-4,
    the one immediately before. Labelling the newest exchange took that from 0/2 to 2/2.
    """
    from app.agent.followup import build_history_prompt

    history = [
        ConversationTurn(
            query="What is the status of ASK-5?", answer="In Progress.", retrieval_grade="correct"
        ),
        ConversationTurn(
            query="What is the status of ASK-4?", answer="Done.", retrieval_grade="correct"
        ),
    ]

    prompt = build_history_prompt(history, "who is it assigned to?")

    assert prompt.index("Earlier exchange") < prompt.index("MOST RECENT exchange")
    # The marker must sit on the newest turn, not merely appear somewhere.
    assert prompt.index("ASK-4") > prompt.index("MOST RECENT exchange")
