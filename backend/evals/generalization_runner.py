"""Prove the system answers a project whose data nobody wrote test cases for.

Every other eval dataset here is a file of hand-written expectations against one corpus. That
guards known behaviour well and cannot demonstrate the central claim — that this works on any
project, not only the one it was built against.

This suite derives every expectation from the database at run time (`generalization_probe`), asks
the question over HTTP, and checks the answer against what SQL said. It carries no knowledge of any
corpus, so the same command runs unchanged against a project synced five minutes ago.

    python -m evals.generalization_runner --project-id askbase
    python -m evals.generalization_runner --project-id <new> --database-url ... --base-url ...

Only deterministic-path questions are asserted. Phrasing the model chooses is measured elsewhere.
"""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.schemas import QueryResponse
from evals.generalization_models import Check, GeneralizationCase, GeneralizationSummary
from evals.generalization_probe import CorpusFacts, probe_corpus

# A name no corpus will contain, used to assert that a miss is reported as a miss.
ABSENT_AUTHOR = "Zzyzx Nonexistent"


def _plan(facts: CorpusFacts) -> list[tuple[str, str, str, list[tuple[str, object]]]]:
    """Build (id, question, derivation, expectations) from what the corpus actually holds.

    Each expectation is a (kind, value) pair checked in `_evaluate`. Cases whose ground truth is
    missing are simply not planned — a project with no Jira yields no Jira cases rather than
    failures that say more about the corpus than about the code.
    """
    plan: list[tuple[str, str, str, list[tuple[str, object]]]] = []

    if facts.top_author and facts.newest_sha:
        plan.append(
            (
                "newest_commit_by_author",
                f"What was the last commit by {facts.top_author}?",
                f"newest commit for {facts.top_author!r} is {facts.newest_sha[:7]}",
                [
                    ("route", "latest_commit"),
                    ("grade", "correct"),
                    ("answer_contains", facts.newest_sha[:7]),
                    ("has_citations", True),
                ],
            )
        )

    if facts.top_author and facts.second_newest_sha:
        plan.append(
            (
                "second_newest_commit_by_author",
                f"What was the second-to-last commit by {facts.top_author}?",
                f"second-newest for {facts.top_author!r} is {facts.second_newest_sha[:7]}",
                [
                    ("route", "latest_commit"),
                    ("grade", "correct"),
                    ("answer_contains", facts.second_newest_sha[:7]),
                    # The whole point of ordinals: it must not fall back to the newest.
                    ("answer_excludes", facts.newest_sha[:7] if facts.newest_sha else None),
                ],
            )
        )

    if facts.sampled_sha:
        plan.append(
            (
                "commit_by_hash",
                f"Tell me about commit {facts.sampled_sha[:7]}.",
                f"commit {facts.sampled_sha[:7]} exists in the corpus",
                [
                    ("route", "commit_detail"),
                    ("grade", "correct"),
                    ("answer_contains", facts.sampled_sha[:7]),
                    ("has_citations", True),
                ],
            )
        )

    if facts.top_author:
        plan.append(
            (
                "ordinal_past_the_end",
                f"What was the {facts.top_author_commits + 5}th commit by {facts.top_author}?",
                f"{facts.top_author!r} has only {facts.top_author_commits} commit(s)",
                [
                    ("route", "latest_commit"),
                    # Refusing is the answer. Substituting the newest commit would be confidently
                    # wrong, and is the failure this asserts against.
                    ("answer_excludes", facts.newest_sha[:7] if facts.newest_sha else None),
                    ("has_citations", False),
                ],
            )
        )

    if facts.commit_count:
        plan.append(
            (
                "absent_author",
                f"What was the last commit by {ABSENT_AUTHOR}?",
                "no such author exists in the corpus",
                [
                    ("route", "latest_commit"),
                    ("has_citations", False),
                    ("answer_excludes", facts.newest_sha[:7] if facts.newest_sha else None),
                ],
            )
        )

    if facts.issue_key and facts.issue_status:
        plan.append(
            (
                "issue_by_key",
                f"What is the status of {facts.issue_key}?",
                f"{facts.issue_key} is {facts.issue_status!r}",
                [
                    ("route", "jira_issue_status"),
                    ("grade", "correct"),
                    ("answer_contains", facts.issue_key),
                    ("answer_contains", facts.issue_status),
                    ("has_citations", True),
                ],
            )
        )

    if facts.assignee and facts.assignee_issue_key:
        plan.append(
            (
                "issues_by_assignee",
                f"Which issues are assigned to {facts.assignee}?",
                f"{facts.assignee!r} is assigned {facts.assignee_issue_key}",
                [
                    ("route", "jira_assignee"),
                    ("grade", "correct"),
                    ("answer_contains", facts.assignee_issue_key),
                    ("has_citations", True),
                ],
            )
        )

    if facts.issue_count:
        plan.append(
            (
                "issue_counts",
                "How many issues are still open?",
                f"the corpus holds {facts.issue_count} Jira issue(s)",
                [
                    ("route", "jira_project_status"),
                    ("grade", "correct"),
                    ("answer_contains", str(facts.issue_count)),
                ],
            )
        )

    return plan


def _evaluate(
    expectations: list[tuple[str, object]], parsed: QueryResponse
) -> list[Check]:
    checks: list[Check] = []
    for kind, value in expectations:
        if value is None:
            continue
        if kind == "route":
            checks.append(
                Check(
                    name=f"route == {value}",
                    passed=parsed.query_type == value,
                    detail=f"got {parsed.query_type!r}",
                )
            )
        elif kind == "grade":
            # An exact answer over stale data is *supposed* to come back `ambiguous` with the
            # staleness disclosed — that is the freshness policy working, not a failure. Asserting
            # a bare `correct` here would mean the suite only passes minutes after a sync, so it
            # accepts either and requires the disclosure when the grade is downgraded.
            stale_gap = any("stale" in gap.casefold() for gap in parsed.unresolved_gaps)
            ok = parsed.retrieval_grade == value or (
                value == "correct" and parsed.retrieval_grade == "ambiguous" and stale_gap
            )
            detail = f"got {parsed.retrieval_grade!r}"
            if ok and parsed.retrieval_grade != value:
                detail += " (downgraded for stale sync, disclosed)"
            checks.append(Check(name=f"grade == {value}", passed=ok, detail=detail))
        elif kind == "answer_contains":
            text = str(value)
            checks.append(
                Check(
                    name=f"answer contains {text!r}",
                    passed=text.casefold() in parsed.answer.casefold(),
                    detail=parsed.answer[:160],
                )
            )
        elif kind == "answer_excludes":
            text = str(value)
            checks.append(
                Check(
                    name=f"answer excludes {text!r}",
                    passed=text.casefold() not in parsed.answer.casefold(),
                    detail=parsed.answer[:160],
                )
            )
        elif kind == "has_citations":
            checks.append(
                Check(
                    name="citations present" if value else "no citations",
                    passed=bool(parsed.citations) is bool(value),
                    detail=f"got {len(parsed.citations)}",
                )
            )
    return checks


async def run(
    project_id: str, database_url: str, base_url: str, api_key: str
) -> GeneralizationSummary:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            facts = await probe_corpus(session, project_id)
    finally:
        await engine.dispose()

    if not facts.commit_count and not facts.issue_count:
        raise SystemExit(
            f"Project {project_id!r} has no indexed GitHub or Jira documents. "
            "Sync it before running the generalization suite."
        )

    cases: list[GeneralizationCase] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=120.0) as client:
        for case_id, query, derivation, expectations in _plan(facts):
            before = time.perf_counter()
            try:
                response = await client.post(
                    "/query",
                    headers={"X-API-Key": api_key},
                    json={"query": query, "project_id": project_id, "include_trace": False},
                )
            except httpx.HTTPError as exc:
                cases.append(
                    GeneralizationCase(
                        id=case_id, query=query, derived_from=derivation, error=str(exc)
                    )
                )
                continue
            duration_ms = round((time.perf_counter() - before) * 1_000)

            if response.status_code != 200:
                cases.append(
                    GeneralizationCase(
                        id=case_id,
                        query=query,
                        derived_from=derivation,
                        duration_ms=duration_ms,
                        error=f"HTTP {response.status_code}: {response.text[:160]}",
                    )
                )
                continue

            parsed = QueryResponse.model_validate(response.json())
            cases.append(
                GeneralizationCase(
                    id=case_id,
                    query=query,
                    derived_from=derivation,
                    query_type=parsed.query_type,
                    grade=parsed.retrieval_grade,
                    citations=len(parsed.citations),
                    answer=parsed.answer,
                    duration_ms=duration_ms,
                    checks=_evaluate(expectations, parsed),
                )
            )

    return GeneralizationSummary(
        project_id=project_id,
        generated_at=datetime.now(UTC).isoformat(),
        corpus=facts.as_counts(),
        cases=cases,
        notes=facts.notes,
    )


def render(summary: GeneralizationSummary) -> str:
    lines = [
        f"# Generalization — {summary.project_id}",
        "",
        f"Generated {summary.generated_at}",
        "",
        "Corpus: "
        + ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in summary.corpus.items()),
        "",
        f"**{sum(1 for c in summary.cases if c.passed)}/{len(summary.cases)} cases passed** "
        f"(rate {summary.pass_rate:.3f})",
        "",
        "| Case | Derived from | Route | Grade | Cites | Result |",
        "|---|---|---|---|---:|---|",
    ]
    for case in summary.cases:
        verdict = "pass" if case.passed else (case.error or "FAIL")
        lines.append(
            f"| `{case.id}` | {case.derived_from} | {case.query_type or '-'} | "
            f"{case.grade or '-'} | {case.citations} | {verdict} |"
        )
    for case in summary.cases:
        if case.passed:
            continue
        lines += ["", f"### {case.id}", "", f"Question: {case.query}", ""]
        if case.error:
            lines.append(f"- error: {case.error}")
        for check in case.failures:
            lines.append(f"- failed `{check.name}` — {check.detail}")
        lines += ["", f"Answer: {case.answer[:400]}"]
    if summary.notes:
        lines += ["", "## Cases not planned", ""] + [f"- {note}" for note in summary.notes]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--base-url", default="http://localhost:8000")
    # Defaults to the configured key so it never has to be typed. A key passed on the command
    # line is visible in the process table to anyone with a shell on the host, and in shell
    # history -- which is how APP_API_KEY ended up in a transcript on 2026-09-04.
    parser.add_argument("--api-key", default=settings.app_api_key)
    parser.add_argument("--fail-under", type=float, default=1.0)
    parser.add_argument("--markdown-report", type=Path)
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()

    summary = asyncio.run(
        run(args.project_id, args.database_url, args.base_url, args.api_key)
    )
    report = render(summary)
    print(report)

    if args.markdown_report:
        args.markdown_report.write_text(report)
    if args.json_report:
        args.json_report.write_text(json.dumps(summary.model_dump(), indent=2))

    if summary.pass_rate < args.fail_under:
        raise SystemExit(
            f"Generalization pass rate {summary.pass_rate:.3f} is below {args.fail_under:.3f}."
        )


if __name__ == "__main__":
    main()
