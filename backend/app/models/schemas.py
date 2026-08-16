from typing import Literal

from pydantic import BaseModel, Field

RetrievalGrade = Literal["correct", "ambiguous", "incorrect"]


class Citation(BaseModel):
    id: int
    source_type: str
    title: str
    url: str | None = None
    timestamp: str | None = None


class EvidenceItem(BaseModel):
    id: str
    source_type: str
    title: str
    snippet: str
    citation_id: int
    authority: float = Field(ge=0, le=1)


class TraceStep(BaseModel):
    name: str
    status: Literal["pending", "running", "completed", "failed"]
    duration_ms: int
    summary: str


class QueryRequest(BaseModel):
    query: str
    project_id: str = "project-atlas"
    include_trace: bool = True


class QueryResponse(BaseModel):
    conversation_id: str
    answer: str
    retrieval_grade: RetrievalGrade
    tools_used: list[str]
    citations: list[Citation]
    evidence: list[EvidenceItem]
    unresolved_gaps: list[str]
    trace: list[TraceStep]


class ProjectSummary(BaseModel):
    id: str
    name: str
    repo: str
    jira_project_key: str | None = None
    status: str
    health: Literal["green", "yellow", "red", "gray"]


class ProjectCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=2, max_length=120)
    repo: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    jira_project_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,19}$")
    status: str = "Active"
    health: Literal["green", "yellow", "red", "gray"] = "gray"


class JiraProjectConfig(BaseModel):
    project_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,19}$")


class TimelineItem(BaseModel):
    id: str
    timestamp: str
    source_type: str
    title: str
    summary: str
