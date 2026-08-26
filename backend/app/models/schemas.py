import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class ConversationTurn(BaseModel):
    """One earlier question and its answer.

    `resolved_query` holds the standalone form when the turn was itself a follow-up, so a chain of
    follow-ups resolves against a real question rather than against another pronoun.
    """

    query: str
    resolved_query: str | None = None
    answer: str
    retrieval_grade: RetrievalGrade
    created_at: str | None = None


class QueryRequest(BaseModel):
    query: str
    # Required. This defaulted to the demo project, so a caller that forgot it got a confident
    # answer about the wrong project instead of an error.
    project_id: str
    include_trace: bool = True
    # Absent means "start a new conversation", which is what every caller before this phase did.
    conversation_id: str | None = None


class QueryResponse(BaseModel):
    conversation_id: str
    answer: str
    retrieval_grade: RetrievalGrade
    tools_used: list[str]
    citations: list[Citation]
    evidence: list[EvidenceItem]
    unresolved_gaps: list[str]
    trace: list[TraceStep]
    # The standalone question this turn was actually answered as, when it differed from what was
    # asked. Null for a self-contained question, which is the common case.
    resolved_query: str | None = None
    # Which answer path handled the question. Exposed because routing is what broke most often and
    # `tools_used` cannot separate `latest_commit` from `commit_detail` — both reach the same tool.
    query_type: str | None = None


class ProjectSummary(BaseModel):
    id: str
    name: str
    repo: str
    jira_project_key: str | None = None
    slack_channel_ids: list[str] = Field(default_factory=list)
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


class SlackProjectConfig(BaseModel):
    # Slack channel ids look like C01234ABCDE; requiring the id rather than a name keeps the
    # indexed scope unambiguous when channels are renamed.
    channel_ids: list[str] = Field(min_length=1, max_length=25)

    @field_validator("channel_ids")
    @classmethod
    def validate_channel_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lstrip("#") for item in value]
        for item in cleaned:
            if not re.fullmatch(r"[CGD][A-Z0-9]{4,}", item):
                raise ValueError(f"Invalid Slack channel id: {item!r}")
        return cleaned


class TimelineItem(BaseModel):
    id: str
    timestamp: str
    source_type: str
    title: str
    summary: str
