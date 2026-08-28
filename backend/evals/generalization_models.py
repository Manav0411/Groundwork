"""Result types for the generalization suite.

Deliberately thinner than `evals/models.py`: a case here has no author-written expectation to
model, because every expectation is derived from the database at run time.
"""

from pydantic import BaseModel


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class GeneralizationCase(BaseModel):
    """One question, its derivation, and how it was answered.

    `derived_from` records the ground truth this case was built from, so a failure report says
    what the database actually held rather than only what the answer said.
    """

    id: str
    query: str
    derived_from: str
    query_type: str | None = None
    grade: str | None = None
    citations: int = 0
    answer: str = ""
    duration_ms: int = 0
    checks: list[Check] = []
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]


class GeneralizationSummary(BaseModel):
    project_id: str
    generated_at: str
    corpus: dict[str, int]
    cases: list[GeneralizationCase]
    # Ground truth the corpus could not supply, so the case was never planned. Recorded rather
    # than silently omitted: "8/8 passed" means something different when two cases were skipped.
    notes: list[str] = []

    @property
    def pass_rate(self) -> float:
        return (
            sum(1 for case in self.cases if case.passed) / len(self.cases) if self.cases else 0.0
        )
