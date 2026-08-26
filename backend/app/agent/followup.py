"""Follow-up resolution.

Routing decides the answer path from identifiers in the question: an issue key, a `by <author>`
clause, the word "commit". A follow-up carries none of them — *"who is it assigned to?"* would fall
through to generic retrieval instead of the deterministic Jira tool that answered the question it
follows. So a follow-up has to become a standalone question before `classify_query` sees it.

Two stages, and the first one matters as much as the second:

1. A deterministic gate decides whether the question needs resolving at all. A self-contained
   question must cost nothing and must never be rewritten — every live eval query is self-contained,
   and a gate that fires on one of them is a regression in the exact-answer paths.
2. A model rewrites the question against recent turns, and the rewrite is then checked: any
   identifier it introduces must already have appeared in the conversation. A model that turns
   `ASK-6` into `ASK-7` would steer deterministic routing to the wrong record, which is exactly
   what routing is kept deterministic to prevent.

Resolution never fails a request. Model unavailable, unparseable output, guard rejection — all fall
back to the original question, and the trace records which happened.
"""

import re

from app.agent.routing import BLOCKER_PATTERN, COMMIT_PATTERN, DECISION_PATTERN
from app.core.config import settings
from app.models.schemas import ConversationTurn
from app.services.llm import OllamaClient
from app.services.structured_github import extract_commit_author
from app.services.structured_jira import ISSUE_KEY_PATTERN, extract_assignee, extract_issue_key

# Personal pronouns only. Bare "this"/"that" is deliberately excluded: "what deployment work was
# done **this** week" is a self-contained question, and treating it as a back-reference would send
# a perfectly good query through a rewrite it does not need.
PRONOUN_PATTERN = re.compile(
    r"\b(it|its|they|them|their|theirs|he|him|his|she|her|hers)\b", re.IGNORECASE
)

# Demonstratives count only when they point at a thing the previous turn established.
DEMONSTRATIVE_PATTERN = re.compile(
    r"\b(that|those|these|the)\s+(one|ones|issue|ticket|commit|change|thread|decision|blocker)\b",
    re.IGNORECASE,
)

# Openers that are follow-ups regardless of length: they continue a sentence rather than start one.
CONNECTIVE_PATTERN = re.compile(
    r"^\s*(and\b|what about\b|how about\b|what else\b|who else\b|anything else\b)", re.IGNORECASE
)

# "Why?" on its own is a follow-up; "Why was bcrypt pinned to version 4?" is not. Length is the
# only cheap signal separating them: a bare "why" question is one carrying no subject of its own.
BARE_WHY_PATTERN = re.compile(r"^\s*why\b", re.IGNORECASE)
BARE_WHY_MAX_WORDS = 4

# SHAs are the other identifier a rewrite could corrupt.
SHA_PATTERN = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)

RESOLUTION_SYSTEM_PROMPT = (
    "You rewrite a follow-up question into a standalone question, using the earlier conversation "
    "to replace pronouns and fill in what was left out. Change nothing else: keep the same intent, "
    "and never introduce an issue key, commit hash, or person absent from the conversation. "
    "If the question is already standalone, return it unchanged. "
    'Reply with JSON only: {"query": "<standalone question>"}'
)


def is_self_contained(query: str) -> bool:
    """True when routing can already decide this question on its own terms.

    Uses the same extractors the router uses, rather than substring checks, so the two cannot drift
    apart. That distinction is load-bearing: "Who is it assigned to?" contains the literal text
    "assigned to" but `extract_assignee` correctly returns None, because no name follows it.
    """
    return bool(
        extract_issue_key(query)
        or extract_commit_author(query)
        or extract_assignee(query)
        or COMMIT_PATTERN.search(query)
        or BLOCKER_PATTERN.search(query)
        or DECISION_PATTERN.search(query)
    )


def needs_resolution(query: str) -> bool:
    """Whether this question depends on earlier turns to be answerable.

    Conservative on purpose. A missed follow-up is answered by generic retrieval, which is merely
    the behaviour before this phase existed. A false positive rewrites a question that was already
    correct, and can move it onto the wrong answer path.
    """
    text = query.strip()
    if not text or is_self_contained(text):
        return False
    if PRONOUN_PATTERN.search(text) or DEMONSTRATIVE_PATTERN.search(text):
        return True
    if CONNECTIVE_PATTERN.search(text):
        return True
    return bool(BARE_WHY_PATTERN.search(text)) and len(text.split()) <= BARE_WHY_MAX_WORDS


def extract_identifiers(text: str) -> set[str]:
    """Issue keys and commit hashes, casefolded, for comparing a rewrite against its source."""
    identifiers = {match.group(1).casefold() for match in ISSUE_KEY_PATTERN.finditer(text)}
    identifiers |= {match.group(0).casefold() for match in SHA_PATTERN.finditer(text)}
    return identifiers


def introduces_unknown_identifier(rewritten: str, sources: list[str]) -> bool:
    """True when the rewrite names a record that appears nowhere in the conversation.

    The failure this prevents is quiet and severe: a rewrite of `ASK-6` into `ASK-7` produces a
    confident, correctly cited answer about the wrong ticket.
    """
    known: set[str] = set()
    for source in sources:
        known |= extract_identifiers(source)
    return bool(extract_identifiers(rewritten) - known)


def build_history_prompt(history: list[ConversationTurn], query: str) -> str:
    lines: list[str] = []
    for turn in history:
        # The standalone form of an earlier turn, so a chain of follow-ups resolves against a
        # question rather than against another pronoun.
        lines.append(f"Q: {turn.resolved_query or turn.query}")
        lines.append(f"A: {turn.answer}")
    lines.append(f"Follow-up question: {query}")
    return "\n".join(lines)


async def resolve_followup(
    query: str,
    history: list[ConversationTurn],
    ollama: OllamaClient | None = None,
) -> str | None:
    """Rewrite a follow-up into a standalone question, or return None to use the original.

    Returns None rather than raising for every failure mode, so the caller has one degradation path
    instead of a try/except around routing.
    """
    if not history:
        return None
    client = ollama or OllamaClient()
    try:
        payload = await client.generate_json(
            RESOLUTION_SYSTEM_PROMPT,
            build_history_prompt(history, query),
            model=settings.grader_model,
            timeout_seconds=settings.grader_timeout_seconds,
        )
    except Exception:
        return None

    rewritten = str(payload.get("query") or "").strip()
    if not rewritten or len(rewritten) > 500:
        return None
    if rewritten.casefold() == query.strip().casefold():
        return None

    sources = [query] + [turn.resolved_query or turn.query for turn in history]
    sources += [turn.answer for turn in history]
    if introduces_unknown_identifier(rewritten, sources):
        return None
    return rewritten
