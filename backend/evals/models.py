from typing import Literal

from pydantic import BaseModel, Field, model_validator

ExpectedOutcome = Literal[
    "found",
    "ambiguous",
    "not_found",
    "missing_author",
    "project_not_onboarded",
]


class EvaluationCase(BaseModel):
    id: str
    category: str
    query: str
    project_id: str = "askbase"
    expected_outcome: ExpectedOutcome
    expected_grade: Literal["correct", "ambiguous", "incorrect"]
    expected_citations: int = Field(ge=0)
    source_type: Literal["github", "jira"] = "github"
    expected_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    expected_issue_key: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*-[0-9]+$")
    expected_author: str | None = None
    expected_title: str | None = None
    answer_contains: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(
        default_factory=lambda: ["planner", "structured_github_query"]
    )
    required_trace_steps: list[str] = Field(
        default_factory=lambda: [
            "Input Guardrail",
            "Planner",
            "Structured GitHub Query",
            "Citation Validator",
        ]
    )
    expect_unresolved_gap: bool = False
    max_latency_ms: int = Field(default=2_000, gt=0)
    semantic_reference: str | None = None

    @model_validator(mode="after")
    def validate_found_case(self) -> "EvaluationCase":
        expected_identifier = (
            self.expected_sha if self.source_type == "github" else self.expected_issue_key
        )
        if self.expected_outcome == "found" and expected_identifier is None:
            raise ValueError("found cases require the source identifier")
        if self.expected_outcome != "found" and (self.expected_sha or self.expected_issue_key):
            raise ValueError("only found cases may declare a source identifier")
        if self.source_type == "github" and self.expected_issue_key is not None:
            raise ValueError("GitHub cases cannot declare expected_issue_key")
        if self.source_type == "jira" and self.expected_sha is not None:
            raise ValueError("Jira cases cannot declare expected_sha")
        return self


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str


class CaseResult(BaseModel):
    case_id: str
    category: str
    passed: bool
    score: float = Field(ge=0, le=1)
    duration_ms: int
    checks: list[CheckResult]
    answer: str
    semantic_score: float | None = None
    semantic_reason: str | None = None


class EvaluationSummary(BaseModel):
    dataset: str
    started_at: str
    completed_at: str
    total_cases: int
    passed_cases: int
    pass_rate: float
    mean_score: float
    mean_latency_ms: float
    p95_latency_ms: int
    results: list[CaseResult]
