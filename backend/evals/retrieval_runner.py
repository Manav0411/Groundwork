"""Measure the retriever directly, without the LLM or the HTTP layer.

The existing `evals.runner` drives `/query` and checks the answer contract. This one calls
`hybrid_retrieve` against Postgres so retrieval quality can be isolated, swept across k, and run
without a chat model. It is the instrument used to justify any change to ranking.
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import SourceDocument
from app.services.llm import OllamaClient
from app.services.retrieval import RetrievedRecord, hybrid_retrieve
from evals.retrieval_models import CaseMetrics, RetrievalCase, RetrievalSummary

DEFAULT_KS = (3, 5, 8)
# Default to the application's configured database. Run this harness inside the backend container
# (`docker compose run --rm backend python -m evals.retrieval_runner`): a separate host PostgreSQL
# commonly owns localhost:5432 and shadows the published container port, which surfaces as a
# confusing "role does not exist". Host-port access is for diagnostics only.
DEFAULT_DATABASE_URL = settings.database_url


def load_cases(path: Path) -> list[RetrievalCase]:
    cases = [
        RetrievalCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    if not cases:
        raise ValueError(f"Dataset is empty: {path}")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Dataset contains duplicate case ids: {path}")
    return cases


async def _external_ids(
    session: AsyncSession, records: Sequence[RetrievedRecord]
) -> list[str]:
    """Map retrieved chunks back to their documents' stable external ids, preserving rank order."""
    if not records:
        return []
    document_ids = {record.document_id for record in records}
    rows = (
        await session.execute(
            select(SourceDocument.id, SourceDocument.external_id).where(
                SourceDocument.id.in_(document_ids)
            )
        )
    ).all()
    by_id = {row.id: row.external_id for row in rows}
    ordered: list[str] = []
    for record in records:
        external_id = by_id.get(record.document_id)
        # Deduplicate: two chunks of one document count once for recall.
        if external_id is not None and external_id not in ordered:
            ordered.append(external_id)
    return ordered


async def _document_external_ids(
    session: AsyncSession, records: Sequence[RetrievedRecord]
) -> dict[int, str]:
    if not records:
        return {}
    rows = (
        await session.execute(
            select(SourceDocument.id, SourceDocument.external_id).where(
                SourceDocument.id.in_({r.document_id for r in records})
            )
        )
    ).all()
    return {row.id: row.external_id for row in rows}


def score_case(
    case: RetrievalCase,
    retrieved: list[str],
    records: Sequence[RetrievedRecord],
    ks: Sequence[int],
    relevant_by_rank: Sequence[bool] = (),
) -> CaseMetrics:
    relevant = set(case.relevant_external_ids)
    recall: dict[int, float] = {}
    precision: dict[int, float] = {}
    for k in ks:
        top_k = retrieved[:k]
        hits = len(relevant.intersection(top_k))
        # A negative case has nothing to recall; it scores 1.0 exactly when it returns nothing.
        recall[k] = (hits / len(relevant)) if relevant else (1.0 if not top_k else 0.0)
        precision[k] = (hits / len(top_k)) if top_k else (1.0 if not relevant else 0.0)

    reciprocal_rank = 0.0
    if relevant:
        for rank, external_id in enumerate(retrieved, start=1):
            if external_id in relevant:
                reciprocal_rank = 1 / rank
                break

    return CaseMetrics(
        case_id=case.id,
        category=case.category,
        query=case.query,
        retrieved=retrieved,
        relevant=sorted(relevant),
        recall_at_k=recall,
        precision_at_k=precision,
        reciprocal_rank=reciprocal_rank,
        lexical_hits=sum(1 for record in records if record.lexical_score > 0),
        returned=len(records),
        top_vector_score=max((r.vector_score for r in records), default=0.0),
        min_vector_score=min((r.vector_score for r in records), default=0.0),
        relevant_vector_scores=[
            round(record.vector_score, 4)
            for record, is_relevant in zip(records, relevant_by_rank, strict=False)
            if is_relevant
        ],
    )


async def run(
    cases: list[RetrievalCase],
    *,
    dataset_name: str,
    database_url: str,
    ks: Sequence[int],
    use_embeddings: bool,
) -> RetrievalSummary:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ollama = OllamaClient() if use_embeddings else None
    results: list[CaseMetrics] = []
    try:
        async with factory() as session:
            for case in cases:
                records = await hybrid_retrieve(
                    session,
                    project_id=case.project_id,
                    query=case.query,
                    limit=max(ks),
                    ollama=ollama,
                )
                retrieved = await _external_ids(session, records)
                relevant = set(case.relevant_external_ids)
                doc_ids = await _document_external_ids(session, records)
                flags = [doc_ids.get(r.document_id) in relevant for r in records]
                results.append(score_case(case, retrieved, records, ks, flags))
    finally:
        await engine.dispose()

    negatives = [item for item in results if item.category == "negative"]
    total_returned = sum(item.returned for item in results)
    total_lexical = sum(item.lexical_hits for item in results)
    scored = [item for item in results if item.category != "negative"]

    return RetrievalSummary(
        dataset=dataset_name,
        completed_at=datetime.now(UTC).isoformat(),
        embeddings_enabled=use_embeddings,
        total_cases=len(results),
        ks=list(ks),
        mean_recall_at_k={
            k: sum(item.recall_at_k[k] for item in results) / len(results) for k in ks
        },
        mean_precision_at_k={
            k: sum(item.precision_at_k[k] for item in results) / len(results) for k in ks
        },
        mrr=(sum(item.reciprocal_rank for item in scored) / len(scored)) if scored else 0.0,
        lexical_hit_rate=(total_lexical / total_returned) if total_returned else 0.0,
        negative_cases=len(negatives),
        negative_cases_returning_nothing=sum(1 for item in negatives if item.returned == 0),
        results=results,
    )


def render_markdown(summary: RetrievalSummary) -> str:
    ks = summary.ks
    lines = [
        f"# Retrieval report — {summary.dataset}",
        "",
        f"- Completed: {summary.completed_at}",
        f"- Cases: {summary.total_cases}",
        f"- Embeddings: {'enabled' if summary.embeddings_enabled else 'disabled (lexical only)'}",
        "",
        "| Metric | " + " | ".join(f"k={k}" for k in ks) + " |",
        "|---|" + "---:|" * len(ks),
        "| Recall@k | " + " | ".join(f"{summary.mean_recall_at_k[k]:.3f}" for k in ks) + " |",
        "| Precision@k | " + " | ".join(f"{summary.mean_precision_at_k[k]:.3f}" for k in ks) + " |",
        "",
        f"- MRR (excludes negative cases): {summary.mrr:.3f}",
        f"- Lexical hit rate: {summary.lexical_hit_rate:.3f} "
        "— share of returned chunks matching the query lexically at all",
        f"- Negative cases returning nothing: "
        f"{summary.negative_cases_returning_nothing}/{summary.negative_cases}",
        "",
        "| Case | Category | R@" + str(ks[-1]) + " | P@" + str(ks[-1]) + " | RR | Returned | Lex |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary.results:
        lines.append(
            f"| `{item.case_id}` | {item.category} | "
            f"{item.recall_at_k[ks[-1]]:.2f} | {item.precision_at_k[ks[-1]]:.2f} | "
            f"{item.reciprocal_rank:.2f} | {item.returned} | {item.lexical_hits} |"
        )
    misses = [
        item
        for item in summary.results
        if item.category != "negative" and item.recall_at_k[ks[-1]] < 1.0
    ]
    if misses:
        lines.extend(["", "## Incomplete recall", ""])
        for item in misses:
            missing = sorted(set(item.relevant) - set(item.retrieved[: ks[-1]]))
            shown = ", ".join(m[:12] for m in missing)
            lines.append(f"- `{item.case_id}` missed {len(missing)}: {shown}")
    noisy = [item for item in summary.results if item.category == "negative" and item.returned]
    if noisy:
        lines.extend(["", "## Negative cases that returned evidence anyway", ""])
        for item in noisy:
            lines.append(
                f"- `{item.case_id}` returned {item.returned} chunk(s) for: \"{item.query}\""
            )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure hybrid retrieval quality.")
    parser.add_argument("--dataset", type=Path, default=Path("evals/retrieval_dataset.jsonl"))
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--k", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Skip the embedding call to measure the lexical-only degradation path.",
    )
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    cases = load_cases(args.dataset)
    summary = await run(
        cases,
        dataset_name=args.dataset.name,
        database_url=args.database_url,
        ks=sorted(args.k),
        use_embeddings=not args.no_embeddings,
    )
    markdown = render_markdown(summary)
    print(markdown, end="")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(summary.model_dump(), indent=2, default=str) + "\n")
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
