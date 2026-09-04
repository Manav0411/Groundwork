import argparse
import asyncio
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.models.schemas import QueryResponse
from evals.deepeval_semantic import evaluate_semantics
from evals.deterministic import evaluate_response
from evals.models import CaseResult, EvaluationCase, EvaluationSummary


def load_cases(path: Path) -> list[EvaluationCase]:
    cases = [
        EvaluationCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Dataset contains duplicate case ids: {path}")
    if not cases:
        raise ValueError(f"Dataset is empty: {path}")
    return cases


def _percentile_95(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


async def run_evaluation(
    cases: list[EvaluationCase],
    *,
    dataset_name: str,
    base_url: str,
    api_key: str,
    sync_before: bool = False,
    semantic: bool = False,
    judge_model: str = "qwen3:8b",
    ollama_base_url: str = "http://localhost:11434",
    transport: httpx.AsyncBaseTransport | None = None,
) -> EvaluationSummary:
    """Run every case and score it.

    `transport` exists so the same cases can be driven against an in-process ASGI app instead of a
    running server. That is what lets CI gate the exact-answer dataset: those cases route to typed
    SQL and make no model call, so with a seeded database they need neither credentials nor Ollama.
    Left as None, this is unchanged -- a plain HTTP client against `base_url`.
    """
    started = datetime.now(UTC)
    results: list[CaseResult] = []
    timeout = httpx.Timeout(60)
    async with httpx.AsyncClient(
        base_url=base_url, timeout=timeout, transport=transport
    ) as client:
        health_response = await client.get("/health")
        health_response.raise_for_status()
        if sync_before:
            project_ids = sorted(
                {
                    case.project_id
                    for case in cases
                    if case.expected_outcome != "project_not_onboarded"
                }
            )
            for project_id in project_ids:
                sources = {case.source_type for case in cases if case.project_id == project_id}
                for source in sorted(sources):
                    limit_param = "max_commits" if source == "github" else "max_issues"
                    sync_response = await client.post(
                        f"/projects/{project_id}/sync/{source}",
                        params={limit_param: 500},
                        headers={"X-API-Key": api_key},
                    )
                    sync_response.raise_for_status()
        for case in cases:
            before = time.perf_counter()
            response = await client.post(
                "/query",
                headers={"X-API-Key": api_key},
                json={
                    "query": case.query,
                    "project_id": case.project_id,
                    "include_trace": True,
                },
            )
            duration_ms = round((time.perf_counter() - before) * 1_000)
            response.raise_for_status()
            query_response = QueryResponse.model_validate(response.json())
            checks = evaluate_response(case, query_response, duration_ms)
            semantic_score = None
            semantic_reason = None
            if semantic and case.semantic_reference:
                semantic_result = await asyncio.to_thread(
                    evaluate_semantics,
                    case,
                    query_response.answer,
                    model_name=judge_model,
                    base_url=ollama_base_url,
                )
                semantic_score = semantic_result.score
                semantic_reason = semantic_result.reason
            passed_checks = sum(check.passed for check in checks)
            score = passed_checks / len(checks)
            results.append(
                CaseResult(
                    case_id=case.id,
                    category=case.category,
                    passed=passed_checks == len(checks),
                    score=score,
                    duration_ms=duration_ms,
                    checks=checks,
                    answer=query_response.answer,
                    semantic_score=semantic_score,
                    semantic_reason=semantic_reason,
                )
            )
    completed = datetime.now(UTC)
    latencies = [result.duration_ms for result in results]
    return EvaluationSummary(
        dataset=dataset_name,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        total_cases=len(results),
        passed_cases=sum(result.passed for result in results),
        pass_rate=sum(result.passed for result in results) / len(results),
        mean_score=sum(result.score for result in results) / len(results),
        mean_latency_ms=sum(latencies) / len(latencies),
        p95_latency_ms=_percentile_95(latencies),
        results=results,
    )


def render_markdown(summary: EvaluationSummary) -> str:
    lines = [
        f"# Evaluation report — {summary.dataset}",
        "",
        f"- Completed: {summary.completed_at}",
        f"- Cases: {summary.passed_cases}/{summary.total_cases} passed",
        f"- Pass rate: {summary.pass_rate:.1%}",
        f"- Mean deterministic score: {summary.mean_score:.3f}",
        f"- Mean latency: {summary.mean_latency_ms:.1f} ms",
        f"- P95 latency: {summary.p95_latency_ms} ms",
        "",
        "| Case | Category | Result | Score | Latency |",
        "|---|---|---:|---:|---:|",
    ]
    for result in summary.results:
        lines.append(
            f"| `{result.case_id}` | {result.category} | "
            f"{'PASS' if result.passed else 'FAIL'} | {result.score:.3f} | "
            f"{result.duration_ms} ms |"
        )
    failures = [result for result in summary.results if not result.passed]
    if failures:
        lines.extend(["", "## Failures", ""])
        for result in failures:
            lines.append(f"### `{result.case_id}`")
            lines.append("")
            for check in result.checks:
                if not check.passed:
                    lines.append(f"- `{check.name}`: {check.detail}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the agent evaluation dataset.")
    parser.add_argument("--dataset", type=Path, default=Path("evals/askbase.jsonl"))
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default="change-me")
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    parser.add_argument("--fail-under", type=float, default=1.0)
    parser.add_argument(
        "--sync-before",
        action="store_true",
        help="Refresh each onboarded source before running cases.",
    )
    parser.add_argument("--semantic", action="store_true")
    parser.add_argument("--judge-model", default="qwen3:8b")
    parser.add_argument("--ollama-base-url", default="http://localhost:11434")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    cases = load_cases(args.dataset)
    summary = await run_evaluation(
        cases,
        dataset_name=args.dataset.name,
        base_url=args.base_url,
        api_key=args.api_key,
        sync_before=args.sync_before,
        semantic=args.semantic,
        judge_model=args.judge_model,
        ollama_base_url=args.ollama_base_url,
    )
    markdown = render_markdown(summary)
    print(markdown, end="")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(summary.model_dump_json(indent=2) + "\n")
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(markdown)
    return 0 if summary.pass_rate >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
