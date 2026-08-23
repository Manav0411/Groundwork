from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tracing import TraceRecorder
from app.connectors.synthetic_workspace import (
    get_projects,
    get_weekly_brief_evidence,
    is_synthetic_project,
)
from app.connectors.tavily import TavilyConnector, WebResult, web_results_to_response
from app.core.config import settings
from app.db.models import Project
from app.models.schemas import Citation, EvidenceItem, QueryRequest, QueryResponse, RetrievalGrade
from app.services.citations import validate_citations
from app.services.grading import GradeResult, grade_retrieval, rewrite_query
from app.services.ingestion import upsert_project
from app.services.llm import (
    OllamaClient,
    build_answer_prompt,
    fallback_answer_from_evidence,
    fallback_weekly_brief_answer,
)
from app.services.persistence import persist_query_run
from app.services.retrieval import RetrievedRecord, hybrid_retrieve, records_to_response
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


def classify_query(query: str) -> str:
    normalized = query.lower()
    if "commit" in normalized:
        return "latest_commit"
    if extract_issue_key(query):
        return "jira_issue_status"
    if "assigned to" in normalized:
        return "jira_assignee"
    if "blocker" in normalized or "blocked" in normalized:
        return "blocker_investigation"
    if "decision" in normalized:
        return "decision_history"
    return "weekly_project_brief"


def _finalize(
    trace: TraceRecorder,
    answer: str,
    grade: RetrievalGrade,
    citations: list[Citation],
    gaps: list[str],
) -> tuple[str, RetrievalGrade, list[str]]:
    """Run the citation validator as a measured step and apply its verdict."""
    with trace.step("Citation Validator") as step:
        validation = validate_citations(answer, citations)
        step.summary = validation.summary
    if validation.grade_override is not None:
        grade = validation.grade_override
    return validation.answer, grade, [*gaps, *validation.gaps]


async def _run_latest_commit_query(
    request: QueryRequest,
    session: AsyncSession | None,
    project_exists: bool,
) -> QueryResponse:
    trace = TraceRecorder()
    records: list[RetrievedRecord] = []
    unresolved_gaps: list[str] = []
    retrieval_grade: RetrievalGrade = "ambiguous"

    with trace.step("Input Guardrail") as step:
        step.summary = "Validated API access and project reference."

    with trace.step("Planner") as step:
        author = extract_commit_author(request.query)
        step.summary = (
            "Classified query as latest_commit and selected deterministic SQL retrieval."
        )

    with trace.step("Structured GitHub Query") as step:
        if not project_exists:
            answer = (
                f"Project {request.project_id!r} is not onboarded. Create the project and run a "
                "GitHub sync before asking for commit history."
            )
            step.summary = "Project is not available in PostgreSQL."
            unresolved_gaps.append(
                "Project has not been onboarded or the database is unavailable."
            )
        elif author is None:
            answer = "I need an author name, for example: 'What was the last commit by Raghav?'"
            step.summary = "Could not extract a commit author from the question."
            unresolved_gaps.append("Commit author was not specified using a recognizable form.")
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
                retrieval_grade = "correct"
                if lookup.stale:
                    retrieval_grade = "ambiguous"
                    sync_time = (
                        lookup.last_synced_at.isoformat() if lookup.last_synced_at else "never"
                    )
                    unresolved_gaps.append(
                        f"GitHub data may be stale; last successful sync: {sync_time}."
                    )
            elif lookup.status == "ambiguous":
                candidate_text = ", ".join(lookup.candidates)
                answer = (
                    f"The author name {author!r} is ambiguous. Matching authors: {candidate_text}."
                )
                step.summary = "Multiple indexed GitHub authors matched the requested name."
                unresolved_gaps.append(
                    "A more specific author name, login, or email is required."
                )
            else:
                answer = (
                    f"No indexed GitHub commit was found for {author!r} in {request.project_id}. "
                    "Run a GitHub sync or provide the author's login/email."
                )
                step.summary = "No exact or unique partial author match was found."
                unresolved_gaps.append(
                    "No matching commit exists in the currently indexed history."
                )

    evidence, citations = records_to_response(records)
    answer, retrieval_grade, unresolved_gaps = _finalize(
        trace, answer, retrieval_grade, citations, unresolved_gaps
    )

    response = QueryResponse(
        conversation_id=f"conv-{uuid4().hex[:12]}",
        answer=answer,
        retrieval_grade=retrieval_grade,
        tools_used=["planner", "structured_github_query"],
        citations=citations,
        evidence=evidence,
        unresolved_gaps=unresolved_gaps,
        trace=trace.steps,
    )
    if session is not None and project_exists:
        await persist_query_run(session, request, "latest_commit", response, records)
    return response


async def _run_jira_query(
    request: QueryRequest,
    session: AsyncSession | None,
    project_exists: bool,
    query_type: str,
) -> QueryResponse:
    trace = TraceRecorder()
    records: list[RetrievedRecord] = []
    unresolved_gaps: list[str] = []
    retrieval_grade: RetrievalGrade = "ambiguous"

    with trace.step("Input Guardrail") as step:
        step.summary = "Validated API access and project reference."

    with trace.step("Planner") as step:
        step.summary = f"Classified query as {query_type} and selected deterministic Jira SQL."

    with trace.step("Structured Jira Query") as step:
        if not project_exists or session is None:
            answer = (
                f"Project {request.project_id!r} is not onboarded. Create the project and run a "
                "Jira sync before asking about work items."
            )
            step.summary = "Jira query could not run."
            unresolved_gaps.append(
                "Project has not been onboarded or the database is unavailable."
            )
            lookup = None
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
                retrieval_grade = "correct"
                step.summary = f"Found exact Jira work item {issue.key}."
            else:
                answer = f"No indexed Jira work item was found for {issue_key!r}. Run a Jira sync."
                unresolved_gaps.append(
                    "The requested Jira work item is not in the current index."
                )
                step.summary = "No exact Jira key match was found."
        elif query_type == "jira_assignee":
            assignee = extract_assignee(request.query)
            if assignee is None:
                lookup = None
                answer = "I need an assignee, for example: 'Which issues are assigned to Manav?'"
                unresolved_gaps.append(
                    "An assignee was not specified using a recognizable form."
                )
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
                    retrieval_grade = "correct"
                    step.summary = (
                        "Matched Jira assignee identity and returned ordered issues."
                    )
                elif lookup.status == "ambiguous":
                    candidates = ", ".join(lookup.candidates)
                    answer = (
                        f"The assignee {assignee!r} is ambiguous. Matching users: {candidates}."
                    )
                    unresolved_gaps.append("A more specific Jira assignee identity is required.")
                    step.summary = "Multiple Jira assignees matched the requested identity."
                else:
                    answer = f"No indexed Jira issues were found for assignee {assignee!r}."
                    unresolved_gaps.append(
                        "No matching assignee exists in the indexed Jira issues."
                    )
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
                retrieval_grade = "correct"
                step.summary = "Selected open Jira issues labeled blocked or highest priority."
            else:
                answer = "No open Jira blockers were found in the current index."
                retrieval_grade = "correct"
                step.summary = (
                    "No non-done blocked or highest-priority Jira issues were found."
                )

    if lookup is not None and lookup.stale:
        retrieval_grade = "ambiguous"
        sync_time = lookup.last_synced_at.isoformat() if lookup.last_synced_at else "never"
        unresolved_gaps.append(f"Jira data may be stale; last successful sync: {sync_time}.")

    evidence, citations = records_to_response(records)
    answer, retrieval_grade, unresolved_gaps = _finalize(
        trace, answer, retrieval_grade, citations, unresolved_gaps
    )

    response = QueryResponse(
        conversation_id=f"conv-{uuid4().hex[:12]}",
        answer=answer,
        retrieval_grade=retrieval_grade,
        tools_used=["planner", "structured_jira_query"],
        citations=citations,
        evidence=evidence,
        unresolved_gaps=unresolved_gaps,
        trace=trace.steps,
    )
    if session is not None and project_exists:
        await persist_query_run(session, request, query_type, response, records)
    return response


async def run_agent(request: QueryRequest, session: AsyncSession | None = None) -> QueryResponse:
    query_type = classify_query(request.query)
    project_exists = False
    project: Project | None = None
    if session is not None:
        project = await session.get(Project, request.project_id)
        project_exists = project is not None
        if not project_exists:
            synthetic_project = next(
                (item for item in get_projects() if item.id == request.project_id), None
            )
            if synthetic_project is not None:
                await upsert_project(session, synthetic_project)
                await session.flush()
                project_exists = True
                project = await session.get(Project, request.project_id)
    if query_type == "latest_commit":
        return await _run_latest_commit_query(request, session, project_exists)
    if query_type in {"jira_issue_status", "jira_assignee"} or (
        query_type == "blocker_investigation"
        and project is not None
        and project.jira_project_key is not None
    ):
        return await _run_jira_query(request, session, project_exists, query_type)

    trace = TraceRecorder()
    records: list[RetrievedRecord] = []
    unresolved_gaps: list[str] = []

    with trace.step("Input Guardrail") as step:
        step.summary = "Validated API access and normalized project reference."

    with trace.step("Planner") as step:
        step.summary = (
            f"Classified query as {query_type}; selected hybrid full-text/vector retrieval."
        )

    grade_result: GradeResult | None = None
    corrective_notes: list[str] = []
    web_results: list[WebResult] = []

    with trace.step("Hybrid Retriever") as step:
        if session is not None and project_exists:
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

    evidence: list[EvidenceItem]
    citations: list[Citation]

    if not records and is_synthetic_project(request.project_id):
        # The demo workspace is fixture data, not retrieved evidence, so there is nothing to grade.
        with trace.step("Retrieval Grader") as step:
            evidence, citations = get_weekly_brief_evidence(request.project_id)
            retrieval_grade: RetrievalGrade = "ambiguous"
            unresolved_gaps.append(SYNTHETIC_DEMO_GAP)
            step.summary = (
                "Only synthetic demo evidence was available; graded ambiguous and disclosed it."
            )
        tools_used = ["planner", "synthetic_workspace", "retrieval_grader"]
    else:
        with trace.step("Retrieval Grader") as step:
            grade_result = await grade_retrieval(request.query, records, ollama=OllamaClient())
            step.summary = grade_result.summary

        # Corrective retrieval. Each attempt re-retrieves and re-grades; the grade is capped at
        # `ambiguous` once correction was needed, because the answer is not supported by what the
        # first retrieval returned.
        attempt = 0
        while (
            not grade_result.is_sufficient
            and session is not None
            and project_exists
            and attempt < settings.corrective_max_attempts
        ):
            attempt += 1
            with trace.step(f"Corrective Retrieval {attempt}") as step:
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
                    session,
                    project_id=request.project_id,
                    query=search_query,
                    limit=limit,
                    ollama=OllamaClient(),
                )
                grade_result = await grade_retrieval(
                    request.query, records, ollama=OllamaClient(), corrected=True
                )
                corrective_notes.append(action)
                step.summary = f"Attempt {attempt}: {action}. {grade_result.summary}"

        # Last resort: the corpus genuinely does not contain the answer. Search the web, but never
        # let a web-sourced answer be graded `correct`.
        connector = TavilyConnector()
        if not grade_result.is_sufficient and connector.enabled:
            with trace.step("Web Fallback") as step:
                try:
                    web_results = await connector.search(request.query)
                    step.summary = (
                        f"Project sources were insufficient; retrieved {len(web_results)} "
                        "web result(s)."
                    )
                except Exception as exc:
                    step.fail(f"Web search failed: {exc}")

        if web_results:
            evidence, citations = web_results_to_response(web_results)
            records = []
            retrieval_grade = "ambiguous"
            unresolved_gaps.append(WEB_SOURCED_GAP)
        else:
            records = grade_result.kept
            evidence, citations = records_to_response(records)
            retrieval_grade = grade_result.grade
            if not records:
                unresolved_gaps.append(NO_EVIDENCE_GAP)

        tools_used = ["planner"]
        if session is not None and project_exists:
            tools_used += ["postgres_fts", "pgvector"]
        tools_used.append("retrieval_grader")
        if corrective_notes:
            tools_used.append("corrective_retrieval")
        if web_results:
            tools_used.append("web_search")

    evidence_lines = [
        f"[{item.citation_id}] {item.source_type}: {item.title} — {item.snippet}"
        for item in evidence
    ]

    if not evidence:
        # Nothing to ground an answer in, so nothing is generated. Disclose instead of synthesizing.
        answer = NO_EVIDENCE_ANSWER.format(project_id=request.project_id)
    else:
        answer = (
            fallback_answer_from_evidence(request.query, evidence_lines)
            if records
            else fallback_weekly_brief_answer()
        )
        if settings.llm_provider == "ollama":
            with trace.step("Ollama Answer Generator") as step:
                try:
                    system_prompt, user_prompt = build_answer_prompt(
                        request.query, evidence_lines
                    )
                    answer = await OllamaClient().generate(system_prompt, user_prompt)
                    step.summary = (
                        f"Generated answer with local model {settings.ollama_model}."
                    )
                except Exception as exc:
                    if not settings.llm_fallback_enabled:
                        raise
                    step.fail(
                        "Ollama unavailable or model missing; used deterministic "
                        f"fallback answer. Error: {exc}"
                    )

    answer, retrieval_grade, unresolved_gaps = _finalize(
        trace, answer, retrieval_grade, citations, unresolved_gaps
    )

    response = QueryResponse(
        conversation_id=f"conv-{uuid4().hex[:12]}",
        answer=answer,
        retrieval_grade=retrieval_grade,
        tools_used=tools_used,
        citations=citations,
        evidence=evidence,
        unresolved_gaps=unresolved_gaps,
        trace=trace.steps,
    )
    if session is not None and project_exists:
        try:
            await persist_query_run(session, request, query_type, response, records)
        except Exception:
            await session.rollback()
            raise
    return response
