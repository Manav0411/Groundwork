"""Query routing.

Routing stays deterministic on purpose. The categories that are not decided here — blocker
investigation, decision history, weekly brief — all take the same hybrid retrieval path, so an LLM
planner would change the trace summary and add several seconds of CPU inference without changing
which tools run. The exact-answer verticals are the ones where routing matters, and those are
decided by identifiers a regex reads more reliably than a model.
"""

import re

from app.services.structured_github import (
    extract_commit_author,
    extract_commit_offset,
    extract_commit_sha,
)
from app.services.structured_jira import extract_assignee, extract_issue_key
from app.services.structured_slack import (
    SLACK_SUBJECT_PATTERN,
    extract_slack_channel,
)

QueryType = str

COMMIT_PATTERN = re.compile(r"\bcommits?\b", re.IGNORECASE)
# "blocking" and "blocks" were missing, and a second project made that visible: "what is
# blocking the EC2 deployment?" fell through to retrieval and answered from a Slack thread
# about Ollama, while the Jira issue carrying the `blocked` label sat one deterministic lookup
# away. The verb form is at least as natural as the noun.
# A suffix is required, so bare "block" does not match -- "which code block changed?" is not a
# blocker question.
BLOCKER_PATTERN = re.compile(r"\bblock(?:er|ers|ed|ing|s)\b", re.IGNORECASE)
DECISION_PATTERN = re.compile(r"\bdecisions?\b", re.IGNORECASE)

# Superlative commit intent: "the latest commit", "the most recent commit". The structured GitHub
# tool answers exactly one question — which commit is Nth-newest for an author — so a commit
# question with neither an author, a hash, nor a position is not a question it can answer.
LATEST_PATTERN = re.compile(
    r"\b(?:latest|last|most\s+recent|newest|earliest|first)\b", re.IGNORECASE
)

# Quantifier and count questions about the issue set as a whole: "are all the tasks complete?",
# "how many issues are still open?". These have no answer in the RAG path — the grader asks
# whether a passage supports the answer, and a quantifier is answered by the set, not by any
# member — so every chunk is correctly rejected and the question is refused. Counting rows
# answers them exactly. Both halves are required: a work noun without a quantifier is an
# ordinary topic question, and a quantifier without one ("what is left to do?") is too vague to
# claim.
WORK_NOUN_PATTERN = re.compile(
    r"\b(?:tasks?|issues?|tickets?|stor(?:y|ies)|work\s+items?)\b", re.IGNORECASE
)
AGGREGATE_PATTERN = re.compile(
    r"\b(?:all|every|any|none|how\s+many|count|number\s+of|remaining|outstanding|"
    r"complete|completed|done|finished|closed|resolved|open)\b",
    re.IGNORECASE,
)


# "What was the last feature added?", "what changed recently". A recency question about the
# project as a whole rather than about one commit, one thread or one issue. Both halves are
# required: "recent" alone is a qualifier and "feature" alone is a topic question that belongs on
# retrieval.
# "recently" is not in LATEST_PATTERN, and adding it there would widen a pattern the commit, Slack
# and offset rules all depend on. This rule gets its own qualifier instead.
RECENT_QUALIFIER_PATTERN = re.compile(
    r"\b(?:latest|last|most\s+recent|recently|newest)\b", re.IGNORECASE
)
RECENT_SUBJECT_PATTERN = re.compile(
    r"\b(?:features?|changes?|changed|updates?|shipped|released|releases?|added|additions?|"
    r"work|activity|progress)\b",
    re.IGNORECASE,
)


# Recency over Slack threads. Slack had no structured route at all, so "what was the last
# conversation on slack?" reached hybrid retrieval, where the ranking is semantic and the
# question carries no semantics to rank on: the grader rejected every chunk across two
# corrective attempts and the run refused. Ordering rows by their newest message answers it
# exactly, the same way ordering commits does.
SLACK_RECENCY_EXCLUSION = DECISION_PATTERN


def classify_query(query: str) -> QueryType:
    """Pick the retrieval path for a question.

    Precedence is by specificity, not by keyword order. A concrete identifier such as `ASK-6`
    names one record and beats a generic topic word like "commit", which only names a subject
    area. The previous implementation tested "commit" first, so "which commits relate to ASK-6?"
    was routed to GitHub and answered about the wrong system entirely.
    """
    # 1. An explicit issue key identifies a single record.
    if extract_issue_key(query):
        return "jira_issue_status"

    # 2. An explicit assignee clause names a person to filter Jira by.
    if extract_assignee(query):
        return "jira_assignee"

    # 3. A named commit hash is a question about that commit's content, not a request for the
    #    newest one. The structured tool only answers "latest by author" and would otherwise reply
    #    "I need an author name" to a question that already names the exact record it is about.
    #
    #    Unless the question is positional. "What came before f4a941f?" names a hash but asks for a
    #    different commit, so it belongs on the ordered lookup with that hash as its anchor.
    if COMMIT_PATTERN.search(query) and extract_commit_sha(query):
        if extract_commit_offset(query) == 0:
            return "commit_detail"

    # 4. Commit intent the structured GitHub tool can actually answer: it needs an author or a
    #    position to look up. "Which commit dropped the HuggingFace dependency?" has neither — it
    #    describes content — and used to reach the tool anyway and be told an author was required,
    #    for a question no author would have answered. Content questions belong on retrieval.
    if COMMIT_PATTERN.search(query):
        if (
            extract_commit_author(query)
            or extract_commit_offset(query)
            or LATEST_PATTERN.search(query)
        ):
            return "latest_commit"

    # 5. Recency over Slack threads. Both halves are required: "slack" alone is a topic, and
    #    "latest" alone is not about Slack. Decision questions are excluded on the same reasoning
    #    that keeps commit-content questions off the GitHub tool — "the last decision made on
    #    slack" asks what was decided, and the newest thread is not reliably that answer.
    if (
        SLACK_SUBJECT_PATTERN.search(query)
        and LATEST_PATTERN.search(query)
        and not SLACK_RECENCY_EXCLUSION.search(query)
    ):
        return "latest_slack_thread"

    # 6. Recency about the project rather than about one record. Ordered after the commit and
    #    Slack rules, which are more specific -- "the last commit" names the record type it wants,
    #    and this must not take it. Retrieval cannot answer these at all: recency lives in the
    #    ordering, so the writer is handed topically relevant recent-ish work and infers a
    #    superlative nobody wrote down. Measured at 4 failures in 5 runs before this route existed.
    if RECENT_SUBJECT_PATTERN.search(query) and RECENT_QUALIFIER_PATTERN.search(query):
        return "recent_activity"

    # 7. A question about the issue set rather than about one issue. Ordered after the exact
    #    identifiers, which name a single record and are more specific, and before the topic
    #    categories, which would send it to retrieval and get it refused.
    if WORK_NOUN_PATTERN.search(query) and AGGREGATE_PATTERN.search(query):
        return "jira_project_status"

    # 8. Topic categories below here all share the hybrid retrieval path; the distinction only
    #    labels the trace.
    if BLOCKER_PATTERN.search(query):
        return "blocker_investigation"
    if DECISION_PATTERN.search(query):
        return "decision_history"
    return "weekly_project_brief"


def is_structured(query_type: QueryType) -> bool:
    return query_type in {
        "latest_commit",
        "commit_detail",
        "jira_issue_status",
        "jira_assignee",
        "jira_project_status",
        "latest_slack_thread",
        "recent_activity",
    }


def describe_route(query_type: QueryType, query: str) -> str:
    """Explain the routing decision for the trace, naming the evidence that drove it."""
    if query_type == "jira_issue_status":
        key = extract_issue_key(query)
        return f"Matched Jira issue key {key}; selected deterministic Jira SQL."
    if query_type == "jira_assignee":
        return (
            f"Matched assignee {extract_assignee(query)!r}; selected deterministic Jira SQL."
        )
    if query_type == "jira_project_status":
        return (
            "Matched a question about the issue set rather than one issue; selected deterministic "
            "Jira SQL counts."
        )
    if query_type == "latest_commit":
        author = extract_commit_author(query)
        detail = f" for author {author!r}" if author else " with no author named"
        return f"Matched commit intent{detail}; selected deterministic GitHub SQL."
    if query_type == "commit_detail":
        return (
            f"Matched commit {extract_commit_sha(query)}; the question names one record, so "
            "selected deterministic GitHub SQL."
        )
    if query_type == "latest_slack_thread":
        channel = extract_slack_channel(query)
        scope = f" in #{channel}" if channel else " across indexed channels"
        return f"Matched Slack recency intent{scope}; selected deterministic Slack SQL."
    return f"Classified as {query_type}; selected hybrid full-text/vector retrieval."
