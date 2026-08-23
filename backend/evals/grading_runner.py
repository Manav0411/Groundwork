"""Measure the retrieval grader against the labelled retrieval set.

Reuses `evals/retrieval_dataset.jsonl` rather than inventing new labels: it already marks which
documents are relevant per question, and which questions are unanswerable. That makes the two
things worth knowing directly measurable.

  * Sufficiency — does the grader return nothing on the unanswerable cases, and something on the
    answerable ones? The two paraphrase cases are the ones that matter most: they are relevant but
    score *lower* in cosine similarity than the unanswerable ones, so a grader that drops them has
    merely reinvented the distance threshold Phase 2 proved does not work.
  * Verdict quality — precision and recall of the per-chunk judgements against the gold labels.

Run inside the backend container; a host PostgreSQL commonly shadows the published port.
"""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services.grading import grade_retrieval
from app.services.llm import OllamaClient
from app.services.retrieval import hybrid_retrieve
from evals.retrieval_models import RetrievalCase
from evals.retrieval_runner import _document_external_ids, load_cases


class GradingCaseResult(BaseModel):
    case_id: str
    category: str
    query: str
    retrieved: int
    kept: int
    grade: str
    used_model: bool
    sufficiency_correct: bool
    kept_relevant: int
    kept_irrelevant: int
    missed_relevant: int
    duration_ms: int
    summary: str


class GradingSummary(BaseModel):
    dataset: str
    completed_at: str
    grader_model: str
    total_cases: int
    sufficiency_accuracy: float
    negatives_correct: int
    negatives_total: int
    paraphrases_correct: int
    paraphrases_total: int
    verdict_precision: float
    verdict_recall: float
    model_used_cases: int
    mean_latency_ms: float
    max_latency_ms: int
    results: list[GradingCaseResult]


async def run(cases: list[RetrievalCase], *, dataset_name: str) -> GradingSummary:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ollama = OllamaClient()
    results: list[GradingCaseResult] = []
    try:
        async with factory() as session:
            for case in cases:
                records = await hybrid_retrieve(
                    session, project_id=case.project_id, query=case.query, ollama=ollama
                )
                by_document = await _document_external_ids(session, records)
                relevant = set(case.relevant_external_ids)

                started = time.perf_counter()
                grade = await grade_retrieval(case.query, records, ollama=ollama)
                duration_ms = round((time.perf_counter() - started) * 1000)

                kept_ids = {by_document.get(record.document_id) for record in grade.kept}
                retrieved_relevant = {
                    by_document.get(record.document_id) for record in records
                } & relevant
                kept_relevant = len(kept_ids & relevant)
                kept_irrelevant = len(kept_ids - relevant)
                missed_relevant = len(retrieved_relevant - kept_ids)

                # An unanswerable question is graded correctly by keeping nothing; an answerable
                # one by keeping at least one genuinely relevant document.
                sufficiency_correct = (
                    not grade.kept if case.category == "negative" else kept_relevant > 0
                )

                results.append(
                    GradingCaseResult(
                        case_id=case.id,
                        category=case.category,
                        query=case.query,
                        retrieved=len(records),
                        kept=len(grade.kept),
                        grade=grade.grade,
                        used_model=grade.used_model,
                        sufficiency_correct=sufficiency_correct,
                        kept_relevant=kept_relevant,
                        kept_irrelevant=kept_irrelevant,
                        missed_relevant=missed_relevant,
                        duration_ms=duration_ms,
                        summary=grade.summary,
                    )
                )
    finally:
        await engine.dispose()

    negatives = [item for item in results if item.category == "negative"]
    paraphrases = [item for item in results if item.category == "paraphrase"]
    total_kept = sum(item.kept_relevant + item.kept_irrelevant for item in results)
    total_relevant_retrieved = sum(
        item.kept_relevant + item.missed_relevant for item in results
    )
    latencies = [item.duration_ms for item in results]

    return GradingSummary(
        dataset=dataset_name,
        completed_at=datetime.now(UTC).isoformat(),
        grader_model=settings.grader_model,
        total_cases=len(results),
        sufficiency_accuracy=sum(item.sufficiency_correct for item in results) / len(results),
        negatives_correct=sum(item.sufficiency_correct for item in negatives),
        negatives_total=len(negatives),
        paraphrases_correct=sum(item.sufficiency_correct for item in paraphrases),
        paraphrases_total=len(paraphrases),
        verdict_precision=(
            sum(item.kept_relevant for item in results) / total_kept if total_kept else 0.0
        ),
        verdict_recall=(
            sum(item.kept_relevant for item in results) / total_relevant_retrieved
            if total_relevant_retrieved
            else 0.0
        ),
        model_used_cases=sum(item.used_model for item in results),
        mean_latency_ms=sum(latencies) / len(latencies),
        max_latency_ms=max(latencies),
        results=results,
    )


def render_markdown(summary: GradingSummary) -> str:
    lines = [
        f"# Grading report — {summary.dataset}",
        "",
        f"- Completed: {summary.completed_at}",
        f"- Grader model: `{summary.grader_model}`",
        f"- Cases graded by the model: {summary.model_used_cases}/{summary.total_cases}",
        "",
        f"- **Sufficiency accuracy: {summary.sufficiency_accuracy:.3f}**",
        f"- Unanswerable questions correctly refused: "
        f"{summary.negatives_correct}/{summary.negatives_total}",
        f"- Paraphrase questions preserved: "
        f"{summary.paraphrases_correct}/{summary.paraphrases_total}",
        f"- Verdict precision: {summary.verdict_precision:.3f}",
        f"- Verdict recall: {summary.verdict_recall:.3f}",
        f"- Latency: {summary.mean_latency_ms:.0f} ms mean, {summary.max_latency_ms} ms max",
        "",
        "| Case | Category | Retrieved | Kept | Grade | OK | ms |",
        "|---|---|---:|---:|---|:--:|---:|",
    ]
    for item in summary.results:
        mark = "yes" if item.sufficiency_correct else "NO"
        lines.append(
            f"| `{item.case_id}` | {item.category} | {item.retrieved} | {item.kept} | "
            f"{item.grade} | {mark} | {item.duration_ms} |"
        )
    failures = [item for item in summary.results if not item.sufficiency_correct]
    if failures:
        lines.extend(["", "## Sufficiency failures", ""])
        for item in failures:
            expected = "keep nothing" if item.category == "negative" else "keep something relevant"
            lines.append(f'- `{item.case_id}` (expected to {expected}): {item.summary}')
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure the retrieval grader.")
    parser.add_argument("--dataset", type=Path, default=Path("evals/retrieval_dataset.jsonl"))
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    parser.add_argument(
        "--fail-under",
        type=float,
        default=0.0,
        help="Exit non-zero when sufficiency accuracy falls below this.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    summary = await run(load_cases(args.dataset), dataset_name=args.dataset.name)
    markdown = render_markdown(summary)
    print(markdown, end="")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(summary.model_dump(), indent=2, default=str) + "\n")
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(markdown)
    return 0 if summary.sufficiency_accuracy >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
