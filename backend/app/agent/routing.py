"""Query routing.

Routing stays deterministic on purpose. The categories that are not decided here — blocker
investigation, decision history, weekly brief — all take the same hybrid retrieval path, so an LLM
planner would change the trace summary and add several seconds of CPU inference without changing
which tools run. The exact-answer verticals are the ones where routing matters, and those are
decided by identifiers a regex reads more reliably than a model.
"""

import re

from app.services.structured_github import extract_commit_author
from app.services.structured_jira import extract_assignee, extract_issue_key

QueryType = str

COMMIT_PATTERN = re.compile(r"\bcommits?\b", re.IGNORECASE)
BLOCKER_PATTERN = re.compile(r"\bblocke(?:r|rs|d)\b", re.IGNORECASE)
DECISION_PATTERN = re.compile(r"\bdecisions?\b", re.IGNORECASE)


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

    # 3. Commit intent, which the structured GitHub tool can answer exactly.
    if COMMIT_PATTERN.search(query):
        return "latest_commit"

    # 4. Topic categories below here all share the hybrid retrieval path; the distinction only
    #    labels the trace.
    if BLOCKER_PATTERN.search(query):
        return "blocker_investigation"
    if DECISION_PATTERN.search(query):
        return "decision_history"
    return "weekly_project_brief"


def is_structured(query_type: QueryType) -> bool:
    return query_type in {"latest_commit", "jira_issue_status", "jira_assignee"}


def describe_route(query_type: QueryType, query: str) -> str:
    """Explain the routing decision for the trace, naming the evidence that drove it."""
    if query_type == "jira_issue_status":
        key = extract_issue_key(query)
        return f"Matched Jira issue key {key}; selected deterministic Jira SQL."
    if query_type == "jira_assignee":
        return (
            f"Matched assignee {extract_assignee(query)!r}; selected deterministic Jira SQL."
        )
    if query_type == "latest_commit":
        author = extract_commit_author(query)
        detail = f" for author {author!r}" if author else " with no author named"
        return f"Matched commit intent{detail}; selected deterministic GitHub SQL."
    return f"Classified as {query_type}; selected hybrid full-text/vector retrieval."
