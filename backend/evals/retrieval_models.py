from typing import Literal

from pydantic import BaseModel, Field, model_validator

RetrievalCategory = Literal[
    "single_document",
    "lexical_multi",
    "multi_document",
    "paraphrase",
    "cross_source",
    "negative",
]


class RetrievalCase(BaseModel):
    """One labelled retrieval question.

    Relevance is labelled by `external_id` (commit SHA or Jira key), never by chunk id: chunk rows
    are deleted and recreated whenever a document's content hash changes, so a chunk-id gold set
    silently stops meaning anything after the next sync.
    """

    id: str
    category: RetrievalCategory
    query: str
    project_id: str = "askbase"
    relevant_external_ids: list[str] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_negative(self) -> "RetrievalCase":
        if self.category == "negative" and self.relevant_external_ids:
            raise ValueError("negative cases must have no relevant documents")
        if self.category != "negative" and not self.relevant_external_ids:
            raise ValueError("non-negative cases require at least one relevant document")
        if len(self.relevant_external_ids) != len(set(self.relevant_external_ids)):
            raise ValueError("relevant_external_ids contains duplicates")
        return self


class CaseMetrics(BaseModel):
    case_id: str
    category: RetrievalCategory
    query: str
    retrieved: list[str]
    relevant: list[str]
    recall_at_k: dict[int, float]
    precision_at_k: dict[int, float]
    reciprocal_rank: float
    lexical_hits: int
    returned: int
    top_vector_score: float = 0.0
    min_vector_score: float = 0.0
    relevant_vector_scores: list[float] = Field(default_factory=list)


class RetrievalSummary(BaseModel):
    dataset: str
    completed_at: str
    embeddings_enabled: bool
    total_cases: int
    ks: list[int]
    mean_recall_at_k: dict[int, float]
    mean_precision_at_k: dict[int, float]
    mrr: float
    lexical_hit_rate: float
    """Share of all returned chunks that matched the query lexically at all."""

    negative_cases: int
    negative_cases_returning_nothing: int
    """A correct negative case returns no evidence, letting the agent grade `incorrect`."""

    results: list[CaseMetrics]
