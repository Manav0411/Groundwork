"""Run the golden conversation dataset against a live backend.

Mirrors `evals/runner.py`: same client shape, same `--fail-under` gate, same markdown report. The
differences are that a case is a sequence of turns sharing one conversation, and that each case is
run several times so model-dependent expectations can be reported as a rate instead of asserted
once.
"""

import argparse
import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.models.schemas import QueryResponse
from evals.conversation_checks import evaluate_hard, evaluate_measured
from evals.conversation_models import (
    ConversationCase,
    ConversationResult,
    ConversationSummary,
    TrialOutcome,
    TurnOutcome,
)


def load_cases(path: Path) -> list[ConversationCase]:
    cases = [
        ConversationCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Dataset contains duplicate case ids: {path}")
    if not cases:
        raise ValueError(f"Dataset is empty: {path}")
    return cases


async def _run_trial(
    client: httpx.AsyncClient, case: ConversationCase, api_key: str, trial: int
) -> TrialOutcome:
    """One pass through a conversation, always starting a fresh one."""
    conversation_id: str | None = None
    turns: list[TurnOutcome] = []

    for index, expectation in enumerate(case.turns, start=1):
        payload: dict[str, object] = {
            "query": expectation.query,
            "project_id": case.project_id,
            "include_trace": True,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        before = time.perf_counter()
        response = await client.post("/query", headers={"X-API-Key": api_key}, json=payload)
        duration_ms = round((time.perf_counter() - before) * 1_000)
        if response.status_code != 200:
            return TrialOutcome(
                trial=trial,
                turns=turns,
                error=f"turn{index}: HTTP {response.status_code} {response.text[:160]}",
            )

        parsed = QueryResponse.model_validate(response.json())
        # Threading this is what makes it a conversation rather than N unrelated questions.
        conversation_id = parsed.conversation_id
        turns.append(
            TurnOutcome(
                index=index,
                query=expectation.query,
                resolved_query=parsed.resolved_query,
                query_type=parsed.query_type,
                grade=parsed.retrieval_grade,
                citations=len(parsed.citations),
                duration_ms=duration_ms,
                hard_failures=evaluate_hard(expectation, parsed),
                measured=evaluate_measured(expectation, parsed),
            )
        )
    return TrialOutcome(trial=trial, turns=turns)


async def run_conversations(
    cases: list[ConversationCase],
    *,
    dataset_name: str,
    base_url: str,
    api_key: str,
    trials: int,
    sync_before: bool = False,
) -> ConversationSummary:
    started = datetime.now(UTC)
    results: list[ConversationResult] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(180)) as client:
        (await client.get("/health")).raise_for_status()
        if sync_before:
            # Freshness policy downgrades an otherwise-correct answer to `ambiguous`, so without a
            # sync the grade expectations would measure how long ago someone last synced.
            for project_id in sorted({case.project_id for case in cases}):
                for source in ("github", "jira", "slack"):
                    synced = await client.post(
                        f"/projects/{project_id}/sync/{source}",
                        headers={"X-API-Key": api_key},
                    )
                    if synced.status_code not in (200, 400, 404):
                        synced.raise_for_status()
        for case in cases:
            outcomes = [
                await _run_trial(client, case, api_key, trial) for trial in range(1, trials + 1)
            ]
            results.append(
                ConversationResult(
                    case_id=case.id,
                    category=case.category,
                    known_limitation=case.known_limitation,
                    trials=outcomes,
                )
            )
    return ConversationSummary(
        dataset=dataset_name,
        trials=trials,
        started_at=started.isoformat(),
        completed_at=datetime.now(UTC).isoformat(),
        results=results,
    )


def render_markdown(summary: ConversationSummary) -> str:
    gated, limitations = summary.gated, summary.limitations
    lines = [
        f"# Conversation report — {summary.dataset}",
        "",
        f"- Completed: {summary.completed_at}",
        f"- Trials per conversation: {summary.trials}",
        f"- Gated conversations: {sum(r.hard_pass_rate == 1.0 for r in gated)}/{len(gated)} "
        "passing every hard check",
        f"- Hard pass rate: {summary.hard_pass_rate:.1%}",
        f"- Known limitations excluded from the gate: {len(limitations)}",
        "",
        "## Hard checks (gated)",
        "",
        "| Conversation | Category | Hard | Measured |",
        "|---|---|---:|---|",
    ]
    for result in gated:
        rates = result.measured_rates
        summary_text = (
            ", ".join(f"{name} {rate:.0%}" for name, rate in rates.items()) if rates else "—"
        )
        lines.append(
            f"| `{result.case_id}` | {result.category} | "
            f"{'PASS' if result.hard_pass_rate == 1.0 else f'{result.hard_pass_rate:.0%}'} | "
            f"{summary_text} |"
        )

    failures = [result for result in gated if result.hard_pass_rate < 1.0]
    if failures:
        lines.extend(["", "## Hard failures", ""])
        for result in failures:
            lines.append(f"### `{result.case_id}`")
            lines.append("")
            lines.extend(f"- {detail}" for detail in result.hard_failure_detail)
            lines.append("")

    # Reported separately and never folded into the pass rate, so a limitation stays visible
    # instead of becoming a quietly-tolerated failure.
    if limitations:
        lines.extend(["", "## Known limitations (not gated)", ""])
        for result in limitations:
            # Resolved means every check, not just the gated ones. A limitation whose hard checks
            # pass while its measured expectation still fails is not fixed: `commit_feature_detail`
            # emits a citation (hard) and never mentions a feature (measured), and calling that
            # "now passing" would retire a real limitation on a technicality.
            rates = result.measured_rates
            resolved = result.hard_pass_rate == 1.0 and all(
                rate == 1.0 for rate in rates.values()
            )
            status = "now passing — remove the marker" if resolved else "failing as expected"
            lines.append(f"### `{result.case_id}` — {status}")
            lines.append("")
            lines.append(f"{result.known_limitation}")
            lines.append("")
            lines.extend(f"- {detail}" for detail in result.hard_failure_detail)
            lines.extend(
                f"- measured `{name}`: {rate:.0%}"
                for name, rate in result.measured_rates.items()
                if rate < 1.0
            )
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the golden conversation dataset.")
    parser.add_argument("--dataset", type=Path, default=Path("evals/conversations.jsonl"))
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default="change-me")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--fail-under", type=float, default=1.0)
    parser.add_argument("--markdown-report", type=Path)
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--only", help="Run one conversation by id.")
    parser.add_argument(
        "--category",
        action="append",
        help="Run only these categories. Repeatable.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help=(
            "One trial over the deterministic categories only (~3 min instead of ~50). For use "
            "while iterating; the full suite is still the release gate."
        ),
    )
    parser.add_argument(
        "--sync-before",
        action="store_true",
        help="Refresh each source first, so freshness does not decide the grade.",
    )
    return parser.parse_args()


# Everything except the categories that spend their time in retrieval. `exploratory`,
# `cross_source`, `decision`, and `corpus_limit` run the corrective loop and several model calls a
# turn, which is where the ~50 minutes goes. The rest reach a structured path and answer in
# milliseconds once the follow-up is resolved.
FAST_CATEGORIES = (
    "exact_answer",
    "resolution",
    "ambiguity",
    "integrity",
    "aggregate",
    "citations",
    "refusal",
)


async def main() -> int:
    args = parse_args()
    cases = load_cases(args.dataset)
    if args.only:
        cases = [case for case in cases if case.id == args.only]
        if not cases:
            raise SystemExit(f"No conversation with id {args.only!r}")
    categories = tuple(args.category) if args.category else (FAST_CATEGORIES if args.fast else ())
    if categories:
        cases = [case for case in cases if case.category in categories]
        if not cases:
            raise SystemExit(f"No conversations in categories {list(categories)}")
    trials = 1 if args.fast else args.trials
    summary = await run_conversations(
        cases,
        dataset_name=args.dataset.name,
        base_url=args.base_url,
        api_key=args.api_key,
        trials=trials,
        sync_before=args.sync_before,
    )
    markdown = render_markdown(summary)
    print(markdown, end="")
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(markdown)
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(summary.model_dump_json(indent=2) + "\n")
    return 0 if summary.hard_pass_rate >= args.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
