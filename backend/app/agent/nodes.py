"""Agent graph nodes.

Each node does one thing, records one trace step, and returns a partial state update. The three
answer paths previously duplicated the guardrail, planner, and citation-validation logic; here they
share those nodes and differ only in how evidence is gathered.
"""

import re

from app.agent.admission import is_small_talk
from app.agent.followup import (
    carry_forward_author,
    needs_resolution,
    rebuild_positional_question,
    resolve_followup,
)
from app.agent.routing import classify_query, describe_route
from app.agent.state import AgentState
from app.connectors.tavily import TavilyConnector, web_results_to_response
from app.core.config import settings
from app.models.schemas import RetrievalGrade
from app.services.citations import CITATION_MARKER, claim_spans, validate_citations
from app.services.entailment import check_entailment
from app.services.grading import grade_retrieval, rewrite_query
from app.services.llm import (
    build_answer_prompt,
    chat_client,
    embedding_client,
    fallback_answer_from_evidence,
)
from app.services.retrieval import hybrid_retrieve, records_to_response
from app.services.structured_github import (
    COMMIT_WINDOW,
    commit_by_sha,
    describe_offset,
    extract_commit_author,
    extract_commit_offset,
    extract_commit_sha,
    latest_commit_by_author,
    recent_commits,
)
from app.services.structured_jira import (
    extract_assignee,
    extract_issue_key,
    jira_issue_by_key,
    jira_issues_by_assignee,
    jira_project_status,
    open_jira_blockers,
)
from app.services.structured_slack import extract_slack_channel, latest_slack_threads

# Names no connector on purpose. This said "GitHub or Jira" from before Slack existed, and told
# people to sync two of the three sources they had. Listing connectors means the line goes stale
# every time one is added, and it is wrong per project regardless: Jira and Slack are both
# optional, so a named source may not be configured at all.
#
# Worth knowing where this is actually read: the web UI renders its own refusal copy and shows
# only `unresolved_gaps`, so this sentence reaches API callers rather than the app. That makes it
# less visible, not less wrong -- the API is a supported surface and this was false on it.
NO_EVIDENCE_ANSWER = (
    "I could not find any indexed evidence for this question in {project_id}. Sync the project's "
    "connected sources, or rephrase the question, and ask again."
)
NO_EVIDENCE_GAP = (
    "No indexed evidence matched this question, so no part of an answer could be supported."
)
# "What features did the last commit change?" routes correctly and finds the right commit, then
# answers with the commit's metadata as though that were the answer. It is not: a commit message
# records what changed, not which product feature it belonged to, and nothing in the indexed corpus
# maps one to the other. The commit is still worth returning — but the unanswered half is disclosed
# rather than papered over by an answer that looks complete.
COMMIT_CONTENT_PATTERN = re.compile(
    r"\b(?:features?|functionality|capabilit(?:y|ies)|behaviou?rs?)\b", re.IGNORECASE
)
COMMIT_CONTENT_GAP = (
    "Commit messages record what changed, not which feature it belonged to, so the feature this "
    "commit relates to is not answerable from the indexed history."
)
# Answering a greeting with "no evidence supports this" is technically true and useless. What the
# person needs is the shape of a question this system can answer, using the sources it actually has.
GREETING_ANSWER = (
    "Ask me a question about {project_id} and I will answer it from indexed GitHub, Jira and Slack "
    "evidence, with a citation for every claim. For example: \"what was the last commit by "
    "<author>?\", \"what is the status of GW-3?\", or \"why did we choose the grader model?\"."
)
RECENT_ACTIVITY_LIMIT = 3
WEB_SOURCED_GAP = (
    "No indexed project evidence supported this question, so the answer comes from public web "
    "search rather than from this organization's own records."
)


async def guardrail(state: AgentState) -> AgentState:
    """Decide whether the input is a question at all, before anything expensive runs.

    The node used to be a no-op that recorded a trace step claiming to have "validated API access
    and project reference" — work that happens at the route layer, not here. So "Hey" ran the whole
    graph and came back as a refusal.

    A rejected input still returns a full state rather than raising, because the caller renders one
    shape and a greeting is not an error.
    """
    query = state["request"].query
    with state["trace"].step("Input Guardrail") as step:
        if not is_small_talk(query):
            step.summary = "Input reads as a question; admitted to the pipeline."
            return {}

        step.summary = (
            "Input is a greeting, not a question. Stopped before any retrieval or model call."
        )
        return {
            "query_type": "not_a_question",
            "answer": GREETING_ANSWER.format(project_id=state["request"].project_id),
            # Nothing was retrieved, so there is nothing to grade. The schema admits only three
            # values and this is the honest one of them: no evidence supports an answer here.
            "retrieval_grade": "incorrect",
            "tools_used": ["guardrail"],
            "citations": [],
            "evidence": [],
            # Deliberately empty. A gap describes evidence that was sought and missing; nothing was
            # sought, and inventing one would read as a failure rather than a prompt.
            "unresolved_gaps": [],
        }


async def resolve(state: AgentState) -> AgentState:
    """Turn a follow-up into a standalone question before anything routes on it.

    Returns an updated `request` rather than a separate field, so every downstream node keeps
    reading `state["request"].query` and needs no knowledge that multi-turn exists.
    """
    request = state["request"]
    history = state.get("history") or []
    resolved: str | None = None

    with state["trace"].step("Follow-up Resolution") as step:
        if not history:
            step.summary = "First turn in the conversation; nothing to resolve against."
        elif not needs_resolution(request.query):
            step.summary = "Question is self-contained; skipped resolution."
        elif (rebuilt := rebuild_positional_question(request.query, history)) is not None:
            # Position and author are both recoverable without a model, and the model drops each of
            # them readily. Reconstructing directly is exact and skips a ~6s inference call.
            resolved = rebuilt
            step.summary = f"Rebuilt the positional question deterministically as: {rebuilt!r}"
        else:
            resolved = await resolve_followup(request.query, history)
            # The model reliably resolves the ordinal and drops the person. Re-attaching an author
            # the conversation already named is deterministic and cannot invent one, so it runs
            # whether or not the rewrite itself succeeded.
            carried = carry_forward_author(resolved or request.query, history)
            if carried is not None:
                step.summary = f"Carried the author forward; resolved to: {carried!r}"
                resolved = carried
            elif resolved is None:
                # Covers three cases the caller does not need to distinguish: the model was
                # unavailable, it returned something unusable, or the identifier guard rejected it.
                step.summary = (
                    "Could not resolve the follow-up; answering the question as it was asked."
                )
            else:
                step.summary = f"Resolved follow-up to: {resolved!r}"

    if resolved is None:
        return {"resolved_query": None}
    return {
        "request": request.model_copy(update={"query": resolved}),
        "resolved_query": resolved,
        "tools_used": [*state.get("tools_used", []), "followup_resolution"],
    }


async def plan(state: AgentState) -> AgentState:
    """Classify the (possibly resolved) question and record the routing decision.

    Classification lives here rather than in `run_agent` so that it happens *after* resolution —
    routing a follow-up on its unresolved text is the bug this phase exists to fix.
    """
    query_type = classify_query(state["request"].query)
    with state["trace"].step("Planner") as step:
        step.summary = describe_route(query_type, state["request"].query)
    return {"query_type": query_type}


async def structured_github(state: AgentState) -> AgentState:
    request, session = state["request"], state["session"]
    records, gaps = [], []
    grade: RetrievalGrade = "ambiguous"

    with state["trace"].step("Structured GitHub Query") as step:
        author = extract_commit_author(request.query)
        named_sha = (
            extract_commit_sha(request.query)
            if state["query_type"] == "commit_detail"
            else None
        )
        if not state["project_exists"]:
            answer = (
                f"Project {request.project_id!r} is not onboarded. Create the project and run a "
                "GitHub sync before asking for commit history."
            )
            step.summary = "Project is not available in PostgreSQL."
            gaps.append("Project has not been onboarded or the database is unavailable.")
        elif named_sha is not None:
            lookup = await commit_by_sha(session, request.project_id, named_sha)  # type: ignore[arg-type]
            if lookup.status == "found" and lookup.record is not None:
                records = [lookup.record]
                committed_at = (
                    lookup.record.source_timestamp.isoformat()
                    if lookup.record.source_timestamp
                    else "an unknown time"
                )
                commit_author = lookup.author or "an unknown author"
                answer = (
                    f"Commit `{(lookup.sha or named_sha)[:7]}` by {commit_author} — "
                    f"“{lookup.record.title}”, committed at {committed_at} [1]."
                )
                step.summary = f"Resolved commit {named_sha} by exact hash."
                grade = "correct"
                if lookup.stale:
                    grade = "ambiguous"
                    synced = lookup.last_synced_at.isoformat() if lookup.last_synced_at else "never"
                    gaps.append(f"GitHub data may be stale; last successful sync: {synced}.")
            elif lookup.status == "ambiguous":
                answer = (
                    f"The hash {named_sha!r} matches more than one indexed commit: "
                    f"{', '.join(lookup.candidates)}. Use more characters of the hash."
                )
                step.summary = "Commit hash prefix matched multiple commits."
                gaps.append("A longer commit hash is required to identify one commit.")
            else:
                answer = (
                    f"No indexed commit in {request.project_id} has the hash {named_sha!r}. "
                    "Run a GitHub sync, or check the hash."
                )
                step.summary = "No indexed commit matched the hash."
                gaps.append("No commit with that hash exists in the currently indexed history.")
        else:
            offset = extract_commit_offset(request.query)
            # A hash in a positional question anchors the count rather than naming the answer.
            anchor = extract_commit_sha(request.query) if offset else None
            lookup = await latest_commit_by_author(
                session,  # type: ignore[arg-type]
                request.project_id,
                author,
                offset,
                anchor,
            )
            if lookup.status == "found" and lookup.record is not None:
                records = [lookup.record]
                short_sha = (lookup.sha or "unknown")[:7]
                committed_at = (
                    lookup.record.source_timestamp.isoformat()
                    if lookup.record.source_timestamp
                    else "an unknown time"
                )
                # Without an author the sentence is about the project, not a person, so the
                # attribution moves from the subject to a trailing clause rather than being
                # dropped: who made the commit is still worth saying.
                credited = lookup.author or author
                answer = (
                    (
                        f"The {describe_offset(lookup.offset)} indexed commit by "
                        f"{credited} is `{short_sha}`"
                    )
                    if author is not None
                    else (
                        f"The {describe_offset(lookup.offset)} indexed commit in "
                        f"{request.project_id} is `{short_sha}`"
                        + (f", by {credited}" if credited else "")
                    )
                ) + f" — “{lookup.record.title}”, committed at {committed_at} [1]."
                step.summary = (
                    (
                        f"Found the {describe_offset(lookup.offset)} commit by exact author "
                        f"identity and timestamp for {author}."
                    )
                    if author is not None
                    else (
                        f"Found the {describe_offset(lookup.offset)} commit in the project by "
                        "timestamp; no author was named."
                    )
                )
                grade = "correct"
                if lookup.stale:
                    grade = "ambiguous"
                    synced = lookup.last_synced_at.isoformat() if lookup.last_synced_at else "never"
                    gaps.append(f"GitHub data may be stale; last successful sync: {synced}.")
            elif lookup.status == "out_of_range":
                position = lookup.offset + 1
                subject = f"{lookup.author or author} has" if author is not None else "There are"
                scope = "for this author" if author is not None else "for this project"
                # Two different shortfalls, and conflating them asserts something false. Fewer
                # commits than asked for is a fact about the person; hitting the lookup window is a
                # fact about this tool. Saying "has only 100 commits" of someone with 396 is the
                # kind of confident wrongness the deterministic path exists to avoid.
                answer = (
                    (
                        f"This lookup reads the {COMMIT_WINDOW} most recent commits "
                        f"{scope} in {request.project_id}, so it cannot reach the "
                        f"{position}th."
                    )
                    if lookup.available >= COMMIT_WINDOW
                    else (
                        f"{subject} only {lookup.available} indexed commit(s) in "
                        f"{request.project_id}, so there is no {position}th most recent one."
                    )
                )
                step.summary = (
                    f"Requested commit position is beyond the indexed history {scope}."
                )
                gaps.append(
                    f"Only {lookup.available} commit(s) are indexed {scope}; "
                    f"position {position} was requested."
                )
            elif lookup.status == "ambiguous":
                answer = (
                    f"The author name {author!r} is ambiguous. "
                    f"Matching authors: {', '.join(lookup.candidates)}."
                )
                step.summary = "Multiple indexed GitHub authors matched the requested name."
                gaps.append("A more specific author name, login, or email is required.")
            else:
                answer = (
                    (
                        f"No indexed GitHub commit was found for {author!r} in "
                        f"{request.project_id}. Run a GitHub sync or provide the author's "
                        "login/email."
                    )
                    if author is not None
                    else (
                        f"No GitHub commits are indexed for {request.project_id}. "
                        "Run a GitHub sync and ask again."
                    )
                )
                step.summary = (
                    "No exact or unique partial author match was found."
                    if author is not None
                    else "The project has no indexed commit history."
                )
                gaps.append("No matching commit exists in the currently indexed history.")

    if records and COMMIT_CONTENT_PATTERN.search(request.query):
        grade = "ambiguous"
        gaps.append(COMMIT_CONTENT_GAP)

    evidence, citations = records_to_response(records)
    return {
        "records": records,
        "evidence": evidence,
        "citations": citations,
        "retrieval_grade": grade,
        "unresolved_gaps": gaps,
        "answer": answer,
        "tools_used": ["planner", "structured_github_query"],
    }


async def structured_jira(state: AgentState) -> AgentState:
    request, session, query_type = state["request"], state["session"], state["query_type"]
    records, gaps = [], []
    grade: RetrievalGrade = "ambiguous"
    lookup = None

    with state["trace"].step("Structured Jira Query") as step:
        if not state["project_exists"] or session is None:
            answer = (
                f"Project {request.project_id!r} is not onboarded. Create the project and run a "
                "Jira sync before asking about work items."
            )
            step.summary = "Jira query could not run."
            gaps.append("Project has not been onboarded or the database is unavailable.")
        elif query_type == "jira_issue_status":
            issue_key = extract_issue_key(request.query)
            lookup = await jira_issue_by_key(session, request.project_id, issue_key or "")
            if lookup.status == "found":
                issue = lookup.issues[0]
                records = [issue.record]
                answer = (
                    f"{issue.key} is {issue.status} with "
                    f"{issue.priority or 'unspecified'} priority, "
                    f"assigned to {issue.assignee or 'nobody'} — “{issue.summary}” [1]."
                )
                grade = "correct"
                step.summary = f"Found exact Jira work item {issue.key}."
            else:
                answer = f"No indexed Jira work item was found for {issue_key!r}. Run a Jira sync."
                gaps.append("The requested Jira work item is not in the current index.")
                step.summary = "No exact Jira key match was found."
        elif query_type == "jira_assignee":
            assignee = extract_assignee(request.query)
            if assignee is None:
                answer = "I need an assignee, for example: 'Which issues are assigned to Manav?'"
                gaps.append("An assignee was not specified using a recognizable form.")
                step.summary = "Could not extract a Jira assignee from the question."
            else:
                lookup = await jira_issues_by_assignee(session, request.project_id, assignee)
                if lookup.status == "found":
                    records = [issue.record for issue in lookup.issues]
                    details = "; ".join(
                        f"{issue.key} — {issue.summary} ({issue.status}) [{index}]"
                        for index, issue in enumerate(lookup.issues, start=1)
                    )
                    answer = (
                        f"Found {len(lookup.issues)} issue(s) assigned to {assignee}: {details}."
                    )
                    grade = "correct"
                    step.summary = "Matched Jira assignee identity and returned ordered issues."
                elif lookup.status == "ambiguous":
                    answer = (
                        f"The assignee {assignee!r} is ambiguous. "
                        f"Matching users: {', '.join(lookup.candidates)}."
                    )
                    gaps.append("A more specific Jira assignee identity is required.")
                    step.summary = "Multiple Jira assignees matched the requested identity."
                else:
                    answer = f"No indexed Jira issues were found for assignee {assignee!r}."
                    gaps.append("No matching assignee exists in the indexed Jira issues.")
                    step.summary = "No Jira assignee match was found."
        elif query_type == "jira_project_status":
            lookup = await jira_project_status(session, request.project_id)
            if lookup.total == 0:
                answer = (
                    "No Jira issues are indexed for this project, so there is no work to report "
                    "on. Run a Jira sync and ask again."
                )
                gaps.append("No Jira issues are indexed for this project.")
                step.summary = "No Jira issues are indexed."
            elif lookup.complete:
                answer = f"Yes — all {lookup.total} indexed Jira issues are done."
                grade = "correct"
                step.summary = f"Counted {lookup.total} issue(s), all done."
            else:
                records = [issue.record for issue in lookup.outstanding]
                remaining = lookup.total - lookup.done
                details = "; ".join(
                    f"{issue.key} — {issue.summary} ({issue.status}) [{index}]"
                    for index, issue in enumerate(lookup.outstanding, start=1)
                )
                more = (
                    f" and {remaining - len(lookup.outstanding)} more"
                    if remaining > len(lookup.outstanding)
                    else ""
                )
                answer = (
                    f"No — {lookup.done} of {lookup.total} indexed Jira issues are done, and "
                    f"{remaining} are not: {details}{more}."
                )
                grade = "correct"
                step.summary = (
                    f"Counted {lookup.total} issue(s): {lookup.done} done, {remaining} outstanding."
                )
        else:
            lookup = await open_jira_blockers(session, request.project_id)
            if lookup.status == "found":
                records = [issue.record for issue in lookup.issues]
                details = "; ".join(
                    f"{issue.key} — {issue.summary} ({issue.status}, "
                    f"{issue.priority or 'unspecified'} priority) [{index}]"
                    for index, issue in enumerate(lookup.issues, start=1)
                )
                answer = f"Found {len(lookup.issues)} open blocker(s): {details}."
                grade = "correct"
                step.summary = "Selected open Jira issues labeled blocked or highest priority."
            else:
                answer = "No open Jira blockers were found in the current index."
                grade = "correct"
                step.summary = "No non-done blocked or highest-priority Jira issues were found."

    if lookup is not None and lookup.stale:
        grade = "ambiguous"
        synced = lookup.last_synced_at.isoformat() if lookup.last_synced_at else "never"
        gaps.append(f"Jira data may be stale; last successful sync: {synced}.")

    evidence, citations = records_to_response(records)
    return {
        "records": records,
        "evidence": evidence,
        "citations": citations,
        "retrieval_grade": grade,
        "unresolved_gaps": gaps,
        "answer": answer,
        "tools_used": ["planner", "structured_jira_query"],
    }


async def structured_slack(state: AgentState) -> AgentState:
    """The newest indexed thread, optionally scoped to one channel.

    Ordering is by the thread's most recent message, so a long-running thread
    replied to today outranks one started yesterday and abandoned. Like the
    other exact-answer tools this makes no model call at all.
    """
    request, session = state["request"], state["session"]
    records, gaps = [], []
    grade: RetrievalGrade = "ambiguous"
    lookup = None
    channel = extract_slack_channel(request.query)

    with state["trace"].step("Structured Slack Query") as step:
        if not state["project_exists"] or session is None:
            answer = (
                f"Project {request.project_id!r} is not onboarded. Create the project and run a "
                "Slack sync before asking about threads."
            )
            step.summary = "Slack query could not run."
            gaps.append("Project has not been onboarded or the database is unavailable.")
        else:
            lookup = await latest_slack_threads(
                session, request.project_id, channel=channel
            )
            scope = f"#{channel}" if channel else "the indexed channels"
            if lookup.status == "found":
                thread = lookup.threads[0]
                records = [thread.record]
                when = (
                    thread.latest_at.isoformat() if thread.latest_at else "an unrecorded time"
                )
                others = (
                    f" with {', '.join(thread.participants[1:])}"
                    if len(thread.participants) > 1
                    else ""
                )
                answer = (
                    f"The most recent indexed Slack thread in #{thread.channel_name} is "
                    f"\u201c{thread.headline}\u201d, started by "
                    f"{thread.author or 'an unknown author'}{others}. It has "
                    f"{thread.message_count} message(s) and was last active at "
                    f"{when} [1]."
                )
                grade = "correct"
                step.summary = (
                    f"Selected the newest of {lookup.available} indexed thread(s) in {scope}."
                )
            elif lookup.status == "out_of_range":
                answer = (
                    f"Only {lookup.available} thread(s) are indexed for {scope}; "
                    "a later position was requested."
                )
                step.summary = "Requested position exceeds the indexed thread count."
                gaps.append("Fewer indexed threads exist than the position requested.")
            else:
                answer = (
                    f"No Slack thread is indexed for {scope} in {request.project_id}. "
                    "Connect the channel and run a Slack sync."
                )
                step.summary = f"No indexed Slack thread was found for {scope}."
                gaps.append("No Slack thread matching the request exists in the current index.")

    if lookup is not None and lookup.stale:
        grade = "ambiguous"
        synced = lookup.last_synced_at.isoformat() if lookup.last_synced_at else "never"
        gaps.append(f"Slack data may be stale; last successful sync: {synced}.")

    evidence, citations = records_to_response(records)
    return {
        "records": records,
        "evidence": evidence,
        "citations": citations,
        "retrieval_grade": grade,
        "unresolved_gaps": gaps,
        "answer": answer,
        "tools_used": ["planner", "structured_slack_query"],
    }


async def retrieve(state: AgentState) -> AgentState:
    request, session = state["request"], state["session"]
    records = []
    with state["trace"].step("Hybrid Retriever") as step:
        if session is not None and state["project_exists"]:
            records = await hybrid_retrieve(
                session,
                project_id=request.project_id,
                query=request.query,
                ollama=embedding_client(),
            )
        step.summary = (
            f"Retrieved {len(records)} persisted chunk(s) with hybrid full-text/vector search."
            if records
            else "No persisted chunks matched this question."
        )
    tools = ["planner"]
    if session is not None and state["project_exists"]:
        tools += ["postgres_fts", "pgvector"]
    return {"records": records, "tools_used": tools}


async def grade(state: AgentState) -> AgentState:
    request, records = state["request"], state.get("records", [])

    with state["trace"].step("Retrieval Grader") as step:
        result = await grade_retrieval(
            request.query,
            records,
            ollama=chat_client("grader"),
            corrected=state.get("corrected", False),
        )
        step.summary = result.summary

    tools = list(state.get("tools_used", []))
    if "retrieval_grader" not in tools:
        tools.append("retrieval_grader")
    return {"grade_result": result, "tools_used": tools}


async def correct(state: AgentState) -> AgentState:
    """One bounded corrective attempt: rewrite the question, then widen the candidate pool."""
    request, session = state["request"], state["session"]
    attempt = state.get("attempt", 0) + 1

    with state["trace"].step(f"Corrective Retrieval {attempt}") as step:
        if attempt == 1:
            rewritten = await rewrite_query(request.query, chat_client("grader"))
            search_query = rewritten or request.query
            action = (
                f"rewrote the question as {rewritten!r}"
                if rewritten
                else "could not rewrite the question; retried unchanged"
            )
            limit = 8
        else:
            search_query = request.query
            action = "widened the candidate pool"
            limit = 16
        records = await hybrid_retrieve(
            session,  # type: ignore[arg-type]
            project_id=request.project_id,
            query=search_query,
            limit=limit,
            ollama=embedding_client(),
        )
        step.summary = f"Attempt {attempt}: {action}. Re-retrieved {len(records)} chunk(s)."

    tools = list(state.get("tools_used", []))
    if "corrective_retrieval" not in tools:
        tools.append("corrective_retrieval")
    return {"records": records, "attempt": attempt, "corrected": True, "tools_used": tools}


async def web_fallback(state: AgentState) -> AgentState:
    """Last resort: the corpus genuinely lacks the answer, so look outside it."""
    results = []
    with state["trace"].step("Web Fallback") as step:
        try:
            results = await TavilyConnector().search(state["request"].query)
            step.summary = (
                f"Project sources were insufficient; retrieved {len(results)} web result(s)."
            )
        except Exception as exc:
            step.fail(f"Web search failed: {exc}")

    if not results:
        return {"web_results": []}
    evidence, citations = web_results_to_response(results)
    return {
        "web_results": results,
        "records": [],
        "evidence": evidence,
        "citations": citations,
        # An answer assembled from the public internet is never the same claim as one traced to the
        # organization's own records, so it can never be graded `correct`.
        "retrieval_grade": "ambiguous",
        "unresolved_gaps": [*state.get("unresolved_gaps", []), WEB_SOURCED_GAP],
        "tools_used": [*state.get("tools_used", []), "web_search"],
    }


async def settle_evidence(state: AgentState) -> AgentState:
    """Turn the grader's verdict into the evidence the answer may actually use."""
    if state.get("web_results"):
        return {}
    result = state.get("grade_result")
    if result is None:
        return {}
    records = result.kept
    evidence, citations = records_to_response(records)
    gaps = list(state.get("unresolved_gaps", []))
    if not records and NO_EVIDENCE_GAP not in gaps:
        gaps.append(NO_EVIDENCE_GAP)
    return {
        "records": records,
        "evidence": evidence,
        "citations": citations,
        "retrieval_grade": result.grade,
        "unresolved_gaps": gaps,
    }


async def synthesize(state: AgentState) -> AgentState:
    request = state["request"]
    evidence = state.get("evidence", [])
    if not evidence:
        # Nothing to ground an answer in, so nothing is generated. Disclose instead.
        return {"answer": NO_EVIDENCE_ANSWER.format(project_id=request.project_id)}

    evidence_lines = [
        f"[{item.citation_id}] {item.source_type}: {item.title} — {item.snippet}"
        for item in evidence
    ]
    # Used only when the model is unavailable. It restates retrieved evidence and invents
    # nothing, which is the only honest thing to return without a generator.
    answer = fallback_answer_from_evidence(request.query, evidence_lines)
    # This used to be gated on `settings.llm_provider == "ollama"`, which would have disabled
    # generation entirely under any other provider -- that one line was the whole of the previous
    # "provider abstraction". The factory decides which provider serves this role now.
    with state["trace"].step("Answer Generator") as step:
        try:
            client = chat_client("synthesis")
            system_prompt, user_prompt = build_answer_prompt(request.query, evidence_lines)
            answer = await client.generate(system_prompt, user_prompt)
            step.summary = f"Generated answer with {settings.llm_provider} model {client.model}."

            # A good answer that carries no [n] markers is stripped of every citation by the
            # validator, so it reaches the user looking unsupported when it was not. One
            # bounded retry with an explicit instruction is cheaper than that outcome, and
            # more honest than attaching citations the answer never claimed.
            if not CITATION_MARKER.search(answer):
                system_prompt, user_prompt = build_answer_prompt(
                    request.query, evidence_lines, insist_on_citations=True
                )
                retried = await client.generate(system_prompt, user_prompt)
                if CITATION_MARKER.search(retried):
                    answer = retried
                    step.summary += " First attempt cited nothing; retried and it cited."
                else:
                    step.summary += " Cited nothing on both attempts."
        except Exception as exc:
            if not settings.llm_fallback_enabled:
                raise
            step.fail(
                f"{settings.llm_provider} provider unavailable or model missing; used "
                f"deterministic fallback answer. Error: {exc}"
            )
    return {"answer": answer}


async def structured_recent(state: AgentState) -> AgentState:
    """What changed recently, answered from the ordering rather than from similarity.

    Recency is a property of commit time. Semantic retrieval cannot read it, so this question used
    to reach the RAG path, which handed the writer topically relevant recent-ish work and let it
    infer a superlative nobody had written down -- 4 failures in 5 runs, measured in
    `baselines/prompt_fencing_2026-09-05.md`.

    It deliberately answers a narrower question than the one asked. "Which feature was added" is not
    answerable from commit history at all: a commit message records what changed, not which product
    feature it belonged to. So the answer is the changes, and the gap says the rest.
    """
    request, session = state["request"], state["session"]
    records, gaps = [], []
    grade: RetrievalGrade = "ambiguous"

    with state["trace"].step("Recent Activity Query") as step:
        if not state["project_exists"]:
            answer = (
                f"Project {request.project_id!r} is not onboarded. Create the project and run a "
                "GitHub sync before asking what changed."
            )
            step.summary = "Project is not available in PostgreSQL."
            gaps.append("Project has not been onboarded or the database is unavailable.")
        else:
            records, last_synced_at, stale = await recent_commits(
                session,  # type: ignore[arg-type]
                request.project_id,
                limit=RECENT_ACTIVITY_LIMIT,
            )
            if not records:
                answer = (
                    f"No GitHub commits are indexed for {request.project_id}. "
                    "Run a GitHub sync and ask again."
                )
                step.summary = "The project has no indexed commit history."
                gaps.append("No commit history exists in the currently indexed corpus.")
            else:
                lines = []
                for ordinal, record in enumerate(records, start=1):
                    when = (
                        record.source_timestamp.isoformat()
                        if record.source_timestamp
                        else "an unknown time"
                    )
                    lines.append(f"{record.title} ({when}) [{ordinal}]")
                answer = (
                    f"The {len(records)} most recent indexed changes in {request.project_id}, "
                    "newest first: " + "; ".join(lines) + "."
                )
                step.summary = f"Returned the {len(records)} newest commits by commit time."
                grade = "correct"
                # The question usually says "feature". The history cannot answer that, and saying
                # so is the whole reason this route is better than the one it replaced.
                if COMMIT_CONTENT_PATTERN.search(request.query):
                    grade = "ambiguous"
                    gaps.append(COMMIT_CONTENT_GAP)
                if stale:
                    grade = "ambiguous"
                    synced = last_synced_at.isoformat() if last_synced_at else "never"
                    gaps.append(f"GitHub data may be stale; last successful sync: {synced}.")

    evidence, citations = records_to_response(records)
    return {
        "records": records,
        "evidence": evidence,
        "citations": citations,
        "answer": answer,
        "retrieval_grade": grade,
        "unresolved_gaps": gaps,
        "tools_used": ["planner", "structured_recent_query"],
    }


async def entail(state: AgentState) -> AgentState:
    """Check that each cited claim is stated by the evidence it cites.

    On the synthesis edge only. The structured routes reach `validate` by their own edges and are
    deliberately free of any model call, so this placement keeps them that way without a condition.

    An unsupported claim downgrades the grade and is disclosed. The marker is deliberately left in
    place: stripping it is what happens to a marker that provably resolves to nothing, which is a
    fact, whereas this is a judgement, and a wrong one would silently erase a good citation.
    """
    answer = state.get("answer", "")
    evidence = state.get("evidence", [])
    citations = state.get("citations", [])
    spans = claim_spans(answer)

    with state["trace"].step("Entailment Check") as step:
        if not citations or not spans:
            step.summary = "No cited claim to check."
            return {}
        premises = {item.citation_id: item.snippet for item in evidence}
        result = await check_entailment(spans, premises)
        step.summary = result.summary

    if not result.used_model:
        # Measured in production: Groq's per-minute ceiling skipped the check on 3 of 20 answers,
        # and one of those still graded `correct` -- presented exactly like a verified answer, with
        # only the trace saying otherwise. "Could not verify" is not "verified". The grader already
        # settled this for its own outage: `_derived_grade` downgrades and says relevance was not
        # checked, so this does the same.
        return {
            "entailment_result": result,
            "retrieval_grade": "ambiguous",
            "unresolved_gaps": [
                *state.get("unresolved_gaps", []),
                "Claims in this answer were not checked against their cited evidence, so support "
                "for them is unverified rather than confirmed.",
            ],
        }

    if not result.unsupported:
        return {"entailment_result": result}

    # One gap per unsupported claim, quoting it, because "a claim is unsupported" is not actionable
    # without saying which.
    gaps = [
        f"The evidence cited as {' '.join('[' + str(o) + ']' for o in verdict.ordinals)} does not "
        f"state this claim: \u201c{verdict.text[:160]}\u201d"
        for verdict in result.unsupported
    ]
    return {
        "entailment_result": result,
        "retrieval_grade": "ambiguous",
        "unresolved_gaps": [*state.get("unresolved_gaps", []), *gaps],
    }


async def validate(state: AgentState) -> AgentState:
    citations = state.get("citations", [])
    evidence = state.get("evidence", [])
    with state["trace"].step("Citation Validator") as step:
        result = validate_citations(state.get("answer", ""), citations)
        step.summary = result.summary

        # Only what the answer actually cites is presented as a citation. Retrieval can surface 16
        # chunks for a question the model then answers from three of them — or from none — and
        # rendering all 16 under a "Citations" heading implies support the answer never claimed.
        # The full retrieval stays visible in the trace.
        cited = set(result.valid_ordinals)
        kept_citations = [citation for citation in citations if citation.id in cited]
        kept_evidence = [item for item in evidence if item.citation_id in cited]
        dropped = len(citations) - len(kept_citations)
        if dropped:
            step.summary += (
                f" Dropped {dropped} retrieved citation(s) the answer did not reference."
            )

    grade = state.get("retrieval_grade", "ambiguous")
    if result.grade_override is not None:
        grade = result.grade_override
    return {
        "answer": result.answer,
        "citations": kept_citations,
        "evidence": kept_evidence,
        "retrieval_grade": grade,
        "unresolved_gaps": [*state.get("unresolved_gaps", []), *result.gaps],
    }
