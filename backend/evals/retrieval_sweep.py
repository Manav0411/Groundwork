"""Sweep the fusion parameters and record what each configuration scores.

GW-8. `RRF_K`, `CANDIDATE_DEPTH`, `LEXICAL_WEIGHT` and `VECTOR_WEIGHT` were tuned on 16 cases over
44 documents when GitHub was the only source. `baselines/README.md` says the values were "chosen by
sweeping rrf_k x candidate_depth x weights" — but the grid itself was never written down. No list of
values tried, no per-configuration table, no artifact. Only the rrf_k 60->10 comparison survives, as
a comment in `retrieval.py`. Producing that record is as much the point here as the numbers.

Two things this has to get right or the output is worse than useless, because it would look
authoritative:

**Embeddings must actually resolve.** `hybrid_retrieve` swallows embedding failures and silently
degrades to lexical-only. A sweep run against a stopped Ollama would produce a full table of
plausible numbers measuring something else entirely. `_assert_embeddings_resolved` refuses to
report unless vector scores came back.

**Cost.** The query embedding is recomputed inside `hybrid_retrieve` on every call and nothing
memoises it, so a naive sweep is O(configurations x cases) HTTP round-trips. Every configuration
scores the same queries against the same corpus, so the vectors are identical every time: embed
once up front, replay from a cache.
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.services.llm import OllamaClient
from app.services.retrieval import (
    CANDIDATE_DEPTH,
    LEXICAL_WEIGHT,
    RRF_K,
    VECTOR_WEIGHT,
    hybrid_retrieve,
)
from evals.retrieval_models import RetrievalCase, RetrievalSummary
from evals.retrieval_runner import DEFAULT_KS, load_cases, run

# Centred on the shipped values rather than spanning the space: the question is whether the current
# configuration is still the right one, not what the optimum is over all possible corpora.
# `vector_weight` stays at 1.0 throughout because only the ratio between the two weights affects
# ordering -- scaling both is a no-op on the fused score.
RRF_K_GRID = (10, 30, 60)
CANDIDATE_DEPTH_GRID = (30, 60)
LEXICAL_WEIGHT_GRID = (0.0, 0.15, 0.35, 0.5, 1.0)


@dataclass(frozen=True)
class Configuration:
    rrf_k: int
    candidate_depth: int
    lexical_weight: float
    vector_weight: float = 1.0

    @property
    def is_shipped(self) -> bool:
        return (
            self.rrf_k == RRF_K
            and self.candidate_depth == CANDIDATE_DEPTH
            and self.lexical_weight == LEXICAL_WEIGHT
            and self.vector_weight == VECTOR_WEIGHT
        )

    @property
    def label(self) -> str:
        return (
            f"k={self.rrf_k} depth={self.candidate_depth} "
            f"lex={self.lexical_weight:g} vec={self.vector_weight:g}"
        )

    def as_kwargs(self) -> dict[str, object]:
        return {
            "rrf_k": self.rrf_k,
            "candidate_depth": self.candidate_depth,
            "lexical_weight": self.lexical_weight,
            "vector_weight": self.vector_weight,
        }


def build_grid() -> list[Configuration]:
    """Every combination, with the shipped configuration guaranteed present.

    It is included explicitly rather than assumed to fall out of the grid, so the comparison always
    has its reference row even if the grid constants are edited.
    """
    grid = [
        Configuration(rrf_k=rrf_k, candidate_depth=depth, lexical_weight=lexical)
        for rrf_k in RRF_K_GRID
        for depth in CANDIDATE_DEPTH_GRID
        for lexical in LEXICAL_WEIGHT_GRID
    ]
    shipped = Configuration(
        rrf_k=RRF_K,
        candidate_depth=CANDIDATE_DEPTH,
        lexical_weight=LEXICAL_WEIGHT,
        vector_weight=VECTOR_WEIGHT,
    )
    if shipped not in grid:
        grid.insert(0, shipped)
    return grid


class CachedEmbedder:
    """Replays pre-computed query vectors, so a sweep embeds each query once rather than once per
    configuration.

    Duck-types the one method `hybrid_retrieve` calls. A cache miss raises rather than falling back
    to a live call: a silent miss would reintroduce exactly the per-configuration cost this exists
    to remove, and would do it invisibly.
    """

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        missing = [text for text in texts if text not in self._vectors]
        if missing:
            raise KeyError(f"no cached embedding for {missing[0]!r}")
        return [self._vectors[text] for text in texts]


async def embed_queries(cases: list[RetrievalCase]) -> dict[str, list[float]]:
    client = OllamaClient()
    queries = sorted({case.query for case in cases})
    vectors = await client.embed(queries)
    return dict(zip(queries, vectors, strict=True))


async def _assert_embeddings_resolved(database_url: str, case: RetrievalCase, embedder) -> None:
    """Fail loudly if the vector side contributed nothing.

    `hybrid_retrieve` catches embedding errors and carries on lexical-only, so without this a sweep
    against a stopped Ollama reports a complete, plausible, meaningless table.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            records = await hybrid_retrieve(
                session, project_id=case.project_id, query=case.query, limit=8, ollama=embedder
            )
    finally:
        await engine.dispose()
    if not any(record.vector_score > 0 for record in records):
        raise SystemExit(
            "No vector scores came back: embeddings are not resolving, so every number this "
            "would print measures lexical-only retrieval. Check Ollama before sweeping."
        )


async def sweep(
    cases: list[RetrievalCase], *, dataset_name: str, database_url: str, ks: tuple[int, ...]
) -> list[tuple[Configuration, RetrievalSummary]]:
    vectors = await embed_queries(cases)
    embedder = CachedEmbedder(vectors)
    await _assert_embeddings_resolved(database_url, cases[0], embedder)

    scored: list[tuple[Configuration, RetrievalSummary]] = []
    for configuration in build_grid():
        summary = await run(
            cases,
            dataset_name=dataset_name,
            database_url=database_url,
            ks=ks,
            use_embeddings=True,
            embedder=embedder,
            **configuration.as_kwargs(),
        )
        scored.append((configuration, summary))
    return scored


def render_markdown(scored: list[tuple[Configuration, RetrievalSummary]], *, k: int) -> str:
    shipped = next((s for c, s in scored if c.is_shipped), None)
    lines = [
        "# Fusion sweep",
        "",
        f"- Dataset: `{scored[0][1].dataset}`, {scored[0][1].total_cases} cases",
        f"- Configurations: {len(scored)}",
        f"- Completed: {scored[0][1].completed_at}",
        "",
        f"| Configuration | Recall@{k} | MRR | Lexical hit rate | Negatives clean |",
        "|---|---:|---:|---:|---:|",
    ]
    # Ordered by the two metrics that decide adoption, so the reference row's position is itself
    # the answer: if the shipped configuration sorts first, nothing needs changing.
    ranked = sorted(
        scored, key=lambda item: (item[1].mean_recall_at_k[k], item[1].mrr), reverse=True
    )
    for configuration, summary in ranked:
        marker = " **(shipped)**" if configuration.is_shipped else ""
        clean = f"{summary.negative_cases_returning_nothing}/{summary.negative_cases}"
        lines.append(
            f"| {configuration.label}{marker} | {summary.mean_recall_at_k[k]:.3f} | "
            f"{summary.mrr:.3f} | {summary.lexical_hit_rate:.3f} | {clean} |"
        )

    if shipped is not None:
        best_config, best = ranked[0]
        lines += [
            "",
            "## Against the shipped configuration",
            "",
            f"- Shipped: Recall@{k} {shipped.mean_recall_at_k[k]:.3f}, MRR {shipped.mrr:.3f}",
            f"- Best by Recall@{k}: `{best_config.label}` — "
            f"{best.mean_recall_at_k[k]:.3f}, MRR {best.mrr:.3f}",
        ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep hybrid-retrieval fusion parameters.")
    parser.add_argument("--dataset", default="evals/retrieval_dataset.jsonl")
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--k", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument("--decide-at-k", type=int, default=8)
    parser.add_argument("--markdown-report")
    parser.add_argument("--json-report")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    dataset = Path(args.dataset)
    cases = load_cases(dataset)
    ks = tuple(sorted(set(args.k) | {args.decide_at_k}))
    scored = await sweep(
        cases, dataset_name=dataset.name, database_url=args.database_url, ks=ks
    )
    report = render_markdown(scored, k=args.decide_at_k)
    print(report)
    if args.markdown_report:
        Path(args.markdown_report).write_text(report)
    if args.json_report:
        Path(args.json_report).write_text(
            json.dumps(
                [
                    {"configuration": c.as_kwargs(), "summary": s.model_dump()}
                    for c, s in scored
                ],
                indent=2,
                default=str,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
