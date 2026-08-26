"""Agent graph nodes.

Each node does one thing, records one trace step, and returns a partial state update. The three
answer paths previously duplicated the guardrail, planner, and citation-validation logic; here they
share those nodes and differ only in how evidence is gathered.
"""

from app.agent.followup import needs_resolution, resolve_followup
from app.agent.routing import classify_query, describe_route
from app.agent.state import AgentState
from app.connectors.synthetic_workspace import get_weekly_brief_evidence, is_synthetic_project
from app.connectors.tavily import TavilyConnector, web_results_to_response
from app.core.config import settings
from app.models.schemas import RetrievalGrade
from app.services.citations import validate_citations
from app.services.grading import grade_retrieval, rewrite_query
from app.services.llm import (
    OllamaClient,
    build_answer_prompt,
    fallback_answer_from_evidence,
    fallback_weekly_brief_answer,
)
from app.services.retrieval import hybrid_retrieve, records_to_response
from app.services.structured_github import extract_commit_author, latest_commit_by_author
from app.services.structured_jira import (
    extract_assignee,
    extract_issue_key,
    jira_issue_by_key,
    jira_issues_by_assignee,
    open_jira_blockers,
)

NO_EVIDENCE_ANSWER = (
    "I could not find any indexed evidence for this question in {project_id}. Sync the project's "
    "GitHub or Jira sources, or rephrase the question, and ask again."
)
NO_EVIDENCE_GAP = (
    "No indexed evidence matched this question, so no part of an answer could be supported."
)
SYNTHETIC_DEMO_GAP = (
    "This answer uses the built-in synthetic demo workspace, not synchronized project data."
)
WEB_SOURCED_GAP = (
    "No indexed project evidence supported this question, so the answer comes from public web "
    "search rather than from this organization's own records."
)


async def guardrail(state: AgentState) -> AgentState:
    with state["trace"].step("Input Guardrail") as step:
        step.summary = "Validated API access and project reference."
    return {}


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
        else:
            resolved = await resolve_followup(request.query, history)
            if resolved is None:
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
        if not state["project_exists"]:
            answer = (
                f"Project {request.project_id!r} is not onboarded. Create the project and run a "
                "GitHub sync before asking for commit history."
            )
            step.summary = "Project is not available in PostgreSQL."
            gaps.append("Project has not been onboarded or the database is unavailable.")
        elif author is None:
            answer = "I need an author name, for example: 'What was the last commit by Raghav?'"
            step.summary = "Could not extract a commit author from the question."
            gaps.append("Commit author was not specified using a recognizable form.")
        else:
            lookup = await latest_commit_by_author(session, request.project_id, author)  # type: ignore[arg-type]
            if lookup.status == "found" and lookup.record is not None:
                records = [lookup.record]
                short_sha = (lookup.sha or "unknown")[:7]
                committed_at = (
                    lookup.record.source_timestamp.isoformat()
                    if lookup.record.source_timestamp
                    else "an unknown time"
                )
                answer = (
                    f"The latest indexed commit by {lookup.author or author} is `{short_sha}` — "
                    f"“{lookup.record.title}”, committed at {committed_at} [1]."
                )
                step.summary = (
                    f"Found the latest commit by exact author identity and timestamp for {author}."
                )
                grade = "correct"
                if lookup.stale:
                    grade = "ambiguous"
                    synced = lookup.last_synced_at.isoformat() if lookup.last_synced_at else "never"
                    gaps.append(f"GitHub data may be stale; last successful sync: {synced}.")
            elif lookup.status == "ambiguous":
                answer = (
                    f"The author name {author!r} is ambiguous. "
                    f"Matching authors: {', '.join(lookup.candidates)}."
                )
                step.summary = "Multiple indexed GitHub authors matched the requested name."
                gaps.append("A more specific author name, login, or email is required.")
            else:
                answer = (
                    f"No indexed GitHub commit was found for {author!r} in {request.project_id}. "
                    "Run a GitHub sync or provide the author's login/email."
                )
                step.summary = "No exact or unique partial author match was found."
                gaps.append("No matching commit exists in the currently indexed history.")

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


async def retrieve(state: AgentState) -> AgentState:
    request, session = state["request"], state["session"]
    records = []
    with state["trace"].step("Hybrid Retriever") as step:
        if session is not None and state["project_exists"]:
            records = await hybrid_retrieve(
                session,
                project_id=request.project_id,
                query=request.query,
                ollama=OllamaClient(),
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

    # The synthetic demo workspace is fixture data, not retrieved evidence, so there is nothing to
    # grade. It is scoped to the sample projects and can never supply evidence to a real one.
    if not records and is_synthetic_project(request.project_id):
        with state["trace"].step("Retrieval Grader") as step:
            evidence, citations = get_weekly_brief_evidence(request.project_id)
            step.summary = (
                "Only synthetic demo evidence was available; graded ambiguous and disclosed it."
            )
        return {
            "evidence": evidence,
            "citations": citations,
            "retrieval_grade": "ambiguous",
            "unresolved_gaps": [*state.get("unresolved_gaps", []), SYNTHETIC_DEMO_GAP],
            "tools_used": [*state.get("tools_used", []), "synthetic_workspace", "retrieval_grader"],
            "grade_result": None,
        }

    with state["trace"].step("Retrieval Grader") as step:
        result = await grade_retrieval(
            request.query, records, ollama=OllamaClient(), corrected=state.get("corrected", False)
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
            rewritten = await rewrite_query(request.query, OllamaClient())
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
            ollama=OllamaClient(),
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
    # The canned demo brief belongs only to the synthetic sample projects.
    answer = (
        fallback_weekly_brief_answer()
        if is_synthetic_project(request.project_id) and not state.get("records")
        else fallback_answer_from_evidence(request.query, evidence_lines)
    )
    if settings.llm_provider == "ollama":
        with state["trace"].step("Ollama Answer Generator") as step:
            try:
                system_prompt, user_prompt = build_answer_prompt(request.query, evidence_lines)
                answer = await OllamaClient().generate(system_prompt, user_prompt)
                step.summary = f"Generated answer with local model {settings.ollama_model}."
            except Exception as exc:
                if not settings.llm_fallback_enabled:
                    raise
                step.fail(
                    "Ollama unavailable or model missing; used deterministic "
                    f"fallback answer. Error: {exc}"
                )
    return {"answer": answer}


async def validate(state: AgentState) -> AgentState:
    with state["trace"].step("Citation Validator") as step:
        result = validate_citations(state.get("answer", ""), state.get("citations", []))
        step.summary = result.summary
    grade = state.get("retrieval_grade", "ambiguous")
    if result.grade_override is not None:
        grade = result.grade_override
    return {
        "answer": result.answer,
        "retrieval_grade": grade,
        "unresolved_gaps": [*state.get("unresolved_gaps", []), *result.gaps],
    }
