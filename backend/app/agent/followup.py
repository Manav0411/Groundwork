"""Follow-up resolution.

Routing decides the answer path from identifiers in the question: an issue key, a `by <author>`
clause, the word "commit". A follow-up carries none of them — *"who is it assigned to?"* would fall
through to generic retrieval instead of the deterministic Jira tool that answered the question it
follows. So a follow-up has to become a standalone question before `classify_query` sees it.

Two stages, and the first one matters as much as the second:

1. A deterministic gate decides whether the question needs resolving at all. A question naming
   its own record costs nothing and is never rewritten. A question that merely *routes* to an
   exact-answer tool without naming the record it needs is admitted, because standing alone it can
   only ask for what the conversation already said — but it is still only rewritten when history
   exists, which is what keeps the whole eval set untouched.
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
from app.services.structured_github import extract_commit_author, extract_commit_sha
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
    "You rewrite a follow-up question into a standalone question. Use the earlier conversation "
    "only to replace pronouns and fill in the subject that was left out. "
    "The follow-up asks something NEW: keep exactly what it asks for, and never answer it or "
    "return an earlier question. "
    "Never introduce an issue key, commit hash, or person absent from the conversation. "
    "Example — earlier: 'What was the last commit by Alex?' answered 'abc1234 — Fix login retry'. "
    "Follow-up: 'What was it about?' becomes 'What was commit abc1234 about?', "
    "NOT 'What was the last commit by Alex?'. "
    'Reply with JSON only: {"query": "<standalone question>"}'
)


def names_a_record(query: str) -> bool:
    """True when the question identifies the specific record an exact-answer tool needs.

    Uses the same extractors the router uses, rather than substring checks, so the two cannot drift
    apart. That distinction is load-bearing: "Who is it assigned to?" contains the literal text
    "assigned to" but `extract_assignee` correctly returns None, because no name follows it.
    """
    return bool(
        extract_issue_key(query)
        or extract_commit_author(query)
        or extract_commit_sha(query)
        or extract_assignee(query)
    )


def is_underspecified(query: str) -> bool:
    """True when the question routes to an exact-answer tool without naming its record.

    "What features did the last commit change?" routes to the GitHub tool on the word "commit",
    which then has no author to look up and can only ask for one. Standing alone that reply is
    correct — it is a live eval case. Inside a conversation that already established whose commits
    are under discussion, it is a failure to use what was just said.

    A topic word makes a question *routable*, not *answerable*. Conflating the two is what sent
    this question to a dead end instead of to resolution.
    """
    if names_a_record(query):
        return False
    if COMMIT_PATTERN.search(query):
        return True
    # Blocker and decision questions take the retrieval path, which handles a broad question fine.
    # Only a back-reference makes them dependent on the conversation.
    return bool(BLOCKER_PATTERN.search(query) or DECISION_PATTERN.search(query)) and bool(
        PRONOUN_PATTERN.search(query) or DEMONSTRATIVE_PATTERN.search(query)
    )


def needs_resolution(query: str) -> bool:
    """Whether this question depends on earlier turns to be answerable.

    Returning True does not mean a rewrite happens: `resolve_followup` still refuses when there is
    no history. That is what keeps every first-turn question untouched, the whole eval set included,
    even for the underspecified cases this deliberately admits.
    """
    text = query.strip()
    if not text or names_a_record(text):
        return False
    if PRONOUN_PATTERN.search(text) or DEMONSTRATIVE_PATTERN.search(text):
        return True
    if CONNECTIVE_PATTERN.search(text):
        return True
    if is_underspecified(text):
        return True
    return bool(BARE_WHY_PATTERN.search(text)) and len(text.split()) <= BARE_WHY_MAX_WORDS


def is_self_contained(query: str) -> bool:
    """True when the question can be answered without reference to earlier turns."""
    return not needs_resolution(query)


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


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split()).strip(" ?.!")


def restates_an_earlier_turn(rewritten: str, history: list[ConversationTurn]) -> bool:
    """True when the rewrite is just an earlier question repeated back.

    A small model asked to "use the conversation" will sometimes return the previous question
    verbatim rather than resolve the new one. That passes every other check — it differs from the
    follow-up, and it introduces no new identifier — while discarding what was actually asked. Live
    testing caught it: "What was it about?" came back as "What was the last commit by Manav0411?",
    and the agent confidently re-answered the previous turn.
    """
    normalized = _normalize(rewritten)
    return any(
        _normalize(turn.resolved_query or turn.query) == normalized for turn in history
    )


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
    if _normalize(rewritten) == _normalize(query):
        return None
    if restates_an_earlier_turn(rewritten, history):
        return None

    sources = [query] + [turn.resolved_query or turn.query for turn in history]
    sources += [turn.answer for turn in history]
    if introduces_unknown_identifier(rewritten, sources):
        return None
    return rewritten
