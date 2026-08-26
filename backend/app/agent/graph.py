"""The agent graph.

Three answer paths previously existed as three near-duplicate functions, each re-implementing the
guardrail, planner, and citation-validation steps. They are now nodes in one `StateGraph` that
share those stages and differ only in how evidence is gathered.

The graph earns its place because of one edge: `grade -> correct -> grade` is a real cycle with a
bounded attempt budget. Everything else here is a straight line, and a straight line does not need
a graph — but the corrective loop does, and expressing it as edges makes the control flow
inspectable rather than buried in a while loop.
"""

from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import nodes
from app.agent.routing import classify_query
from app.agent.state import AgentState
from app.agent.tracing import TraceRecorder
from app.connectors.synthetic_workspace import get_projects
from app.core.config import settings
from app.db.models import Project
from app.models.schemas import ConversationTurn, QueryRequest, QueryResponse
from app.services.ingestion import upsert_project
from app.services.persistence import load_conversation_history, persist_query_run

__all__ = ["AGENT_GRAPH", "build_graph", "classify_query", "run_agent"]


def _route_after_plan(state: AgentState) -> str:
    query_type = state["query_type"]
    if query_type in {"latest_commit", "commit_detail"}:
        return "structured_github"
    if query_type in {"jira_issue_status", "jira_assignee"}:
        return "structured_jira"
    # Blocker questions reach the Jira tool only when the project actually has Jira configured;
    # otherwise they are ordinary retrieval questions.
    if query_type == "blocker_investigation" and state.get("jira_configured"):
        return "structured_jira"
    return "retrieve"


def _route_after_grade(state: AgentState) -> str:
    result = state.get("grade_result")
    if result is None or result.is_sufficient:
        return "settle_evidence"
    if (
        state.get("session") is not None
        and state.get("project_exists")
        and state.get("attempt", 0) < settings.corrective_max_attempts
    ):
        return "correct"
    if settings.web_fallback_enabled:
        return "web_fallback"
    return "settle_evidence"


def build_graph():
    """Assemble the agent graph. Compiled once at import and reused for every request."""
    builder = StateGraph(AgentState)

    builder.add_node("guardrail", nodes.guardrail)
    builder.add_node("resolve", nodes.resolve)
    builder.add_node("plan", nodes.plan)
    builder.add_node("structured_github", nodes.structured_github)
    builder.add_node("structured_jira", nodes.structured_jira)
    builder.add_node("retrieve", nodes.retrieve)
    builder.add_node("grade", nodes.grade)
    builder.add_node("correct", nodes.correct)
    builder.add_node("web_fallback", nodes.web_fallback)
    builder.add_node("settle_evidence", nodes.settle_evidence)
    builder.add_node("synthesize", nodes.synthesize)
    builder.add_node("validate", nodes.validate)

    builder.add_edge(START, "guardrail")
    # Resolution sits ahead of the planner because routing reads identifiers out of the question
    # text, and a follow-up has none until it is resolved.
    builder.add_edge("guardrail", "resolve")
    builder.add_edge("resolve", "plan")
    builder.add_conditional_edges(
        "plan",
        _route_after_plan,
        {
            "structured_github": "structured_github",
            "structured_jira": "structured_jira",
            "retrieve": "retrieve",
        },
    )

    # The exact-answer paths build their answer from a single database row, so they skip retrieval
    # grading and synthesis entirely and go straight to citation validation. That is what keeps
    # them deterministic and free of any model dependency.
    builder.add_edge("structured_github", "validate")
    builder.add_edge("structured_jira", "validate")

    builder.add_edge("retrieve", "grade")
    builder.add_conditional_edges(
        "grade",
        _route_after_grade,
        {
            "correct": "correct",
            "web_fallback": "web_fallback",
            "settle_evidence": "settle_evidence",
        },
    )
    builder.add_edge("correct", "grade")  # the cycle
    builder.add_edge("web_fallback", "synthesize")
    builder.add_edge("settle_evidence", "synthesize")
    builder.add_edge("synthesize", "validate")
    builder.add_edge("validate", END)

    return builder.compile()


AGENT_GRAPH = build_graph()


async def run_agent(request: QueryRequest, session: AsyncSession | None = None) -> QueryResponse:
    project: Project | None = None
    project_exists = False
    if session is not None:
        project = await session.get(Project, request.project_id)
        project_exists = project is not None
        if not project_exists:
            synthetic = next(
                (item for item in get_projects() if item.id == request.project_id), None
            )
            if synthetic is not None:
                await upsert_project(session, synthetic)
                await session.flush()
                project = await session.get(Project, request.project_id)
                project_exists = project is not None

    # History is loaded here rather than in a node so that an unknown or cross-project conversation
    # id is rejected before any work is done. It feeds only the resolution prompt — never
    # retrieval, evidence, or citations.
    history: list[ConversationTurn] = []
    conversation_id = request.conversation_id
    if session is not None and conversation_id:
        history = await load_conversation_history(
            session,
            conversation_id,
            request.project_id,
            limit=settings.conversation_history_turns,
        )

    trace = TraceRecorder()
    initial: AgentState = {
        "request": request,
        "session": session,
        "project_exists": project_exists,
        "jira_configured": project is not None and project.jira_project_key is not None,
        "history": history,
        "original_query": request.query,
        "resolved_query": None,
        "query_type": "weekly_project_brief",
        "trace": trace,
        "records": [],
        "evidence": [],
        "citations": [],
        "unresolved_gaps": [],
        "tools_used": ["planner"],
        "retrieval_grade": "ambiguous",
        "answer": "",
        "attempt": 0,
        "corrected": False,
        "web_results": [],
    }
    # The corrective cycle is bounded by `corrective_max_attempts`, but give LangGraph a hard stop
    # too, so a routing bug can never produce an unbounded loop in production.
    final = await AGENT_GRAPH.ainvoke(
        initial, config={"recursion_limit": 8 + settings.corrective_max_attempts * 4}
    )

    response = QueryResponse(
        conversation_id=conversation_id or f"conv-{uuid4().hex[:12]}",
        answer=final["answer"],
        retrieval_grade=final["retrieval_grade"],
        tools_used=final["tools_used"],
        citations=final["citations"],
        evidence=final["evidence"],
        unresolved_gaps=final["unresolved_gaps"],
        trace=trace.steps,
        resolved_query=final.get("resolved_query"),
    )
    if session is not None and project_exists:
        try:
            await persist_query_run(
                session,
                # The question as typed is what gets stored as `query`; the standalone form it was
                # answered as is stored alongside it.
                request.model_copy(update={"query": final["original_query"]}),
                final["query_type"],
                response,
                final["records"],
            )
        except Exception:
            await session.rollback()
            raise
    return response
