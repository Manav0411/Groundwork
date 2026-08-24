from typing import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tracing import TraceRecorder
from app.models.schemas import Citation, EvidenceItem, QueryRequest, RetrievalGrade
from app.services.grading import GradeResult
from app.services.retrieval import RetrievedRecord


class AgentState(TypedDict, total=False):
    """State threaded through the agent graph.

    Nodes return partial updates; LangGraph merges them. `trace` is deliberately a single mutable
    recorder rather than a merged list, so steps stay in real execution order even when the
    corrective cycle revisits a node.
    """

    request: QueryRequest
    session: AsyncSession | None
    project_exists: bool
    jira_configured: bool

    query_type: str
    trace: TraceRecorder

    records: list[RetrievedRecord]
    evidence: list[EvidenceItem]
    citations: list[Citation]
    grade_result: GradeResult | None
    web_results: list

    retrieval_grade: RetrievalGrade
    unresolved_gaps: list[str]
    tools_used: list[str]
    answer: str

    attempt: int
    corrected: bool
