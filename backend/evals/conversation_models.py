"""Case and result models for the conversation suite.

Mirrors `evals/models.py`, with one structural difference: a case is a *sequence* of turns sharing
a conversation, and each turn carries its own expectations. That is the whole point — every defect
found by hand in the last five rounds lived in turn two or three, and no single-turn dataset could
have reached it.

Expectations are split into two kinds, because one of them is decided by a 3B model:

- **hard** — what the code decides: route, grade, citation presence, marker validity, forbidden
  text. These gate.
- **measured** — what the model decides: whether a follow-up resolved, and to what. These are run
  repeatedly and reported as a rate, because a red build on model variance is noise.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

CitationExpectation = Literal["none", "some", "any"]


class TurnExpectation(BaseModel):
    """One question and what must be true of its answer."""

    query: str

    # --- hard: decided by code -----------------------------------------------------------------
    expect_route: str | None = None
    """`query_type`, e.g. `latest_commit`, `commit_detail`, `jira_issue_status`."""

    expect_grade: Literal["correct", "ambiguous", "incorrect"] | None = None
    expect_citations: CitationExpectation | int = "any"
    expect_gap: bool | None = None
    answer_excludes: list[str] = Field(default_factory=list)
    """Text that must NOT appear — how a fabrication or a leaked prior answer is caught."""

    # --- measured: decided by the model --------------------------------------------------------
    expect_resolved: bool | None = None
    """Whether this turn should have been rewritten into a standalone question."""

    resolved_contains: list[str] = Field(default_factory=list)
    """Identifiers the resolved question must carry, e.g. the issue key from turn one."""

    answer_contains: list[str] = Field(default_factory=list)
    """Measured rather than hard: phrasing varies even when the retrieved record does not."""

    @model_validator(mode="after")
    def validate_expectations(self) -> "TurnExpectation":
        if isinstance(self.expect_citations, int) and self.expect_citations < 0:
            raise ValueError("expect_citations must be non-negative")
        if self.resolved_contains and self.expect_resolved is False:
            raise ValueError("resolved_contains requires expect_resolved to be true")
        return self


class ConversationCase(BaseModel):
    id: str
    category: str
    project_id: str = "askbase"
    turns: list[TurnExpectation] = Field(min_length=1)

    known_limitation: str | None = None
    """Why this is expected to fail. Reported separately; never fails the gate.

    A marker requires a written reason so the bucket cannot become a place to hide inconvenient
    results. Deleting the marker is how a fix gets recorded.
    """


class TurnOutcome(BaseModel):
    index: int
    query: str
    resolved_query: str | None
    query_type: str | None
    grade: str
    citations: int
    duration_ms: int
    hard_failures: list[str] = Field(default_factory=list)
    measured: dict[str, bool] = Field(default_factory=dict)


class TrialOutcome(BaseModel):
    trial: int
    turns: list[TurnOutcome]
    error: str | None = None

    @property
    def hard_passed(self) -> bool:
        return self.error is None and not any(turn.hard_failures for turn in self.turns)


class ConversationResult(BaseModel):
    case_id: str
    category: str
    known_limitation: str | None
    trials: list[TrialOutcome]

    @property
    def hard_pass_rate(self) -> float:
        return sum(trial.hard_passed for trial in self.trials) / len(self.trials)

    @property
    def measured_rates(self) -> dict[str, float]:
        """Per-check pass rate across every trial and turn, keyed `turn{n}:{check}`."""
        totals: dict[str, list[bool]] = {}
        for trial in self.trials:
            for turn in trial.turns:
                for name, passed in turn.measured.items():
                    totals.setdefault(f"turn{turn.index}:{name}", []).append(passed)
        return {key: sum(values) / len(values) for key, values in sorted(totals.items())}

    @property
    def hard_failure_detail(self) -> list[str]:
        seen: list[str] = []
        for trial in self.trials:
            if trial.error and trial.error not in seen:
                seen.append(trial.error)
            for turn in trial.turns:
                for failure in turn.hard_failures:
                    detail = f"turn{turn.index}: {failure}"
                    if detail not in seen:
                        seen.append(detail)
        return seen


class ConversationSummary(BaseModel):
    dataset: str
    trials: int
    started_at: str
    completed_at: str
    results: list[ConversationResult]

    @property
    def gated(self) -> list[ConversationResult]:
        return [result for result in self.results if result.known_limitation is None]

    @property
    def limitations(self) -> list[ConversationResult]:
        return [result for result in self.results if result.known_limitation is not None]

    @property
    def hard_pass_rate(self) -> float:
        gated = self.gated
        if not gated:
            return 1.0
        return sum(result.hard_pass_rate == 1.0 for result in gated) / len(gated)
