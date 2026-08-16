import re
from urllib.parse import urlparse

from app.models.schemas import QueryResponse
from evals.models import CheckResult, EvaluationCase

GITHUB_COMMIT_PATH = re.compile(r"^/[^/]+/[^/]+/commit/([0-9a-f]{40})$")
JIRA_ISSUE_PATH = re.compile(r"^/browse/([A-Z][A-Z0-9_]*-[0-9]+)$")


def _check(name: str, passed: bool, detail: str) -> CheckResult:
    return CheckResult(name=name, passed=passed, detail=detail)


def _valid_github_commit_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() == "github.com"
        and GITHUB_COMMIT_PATH.fullmatch(parsed.path) is not None
    )


def _valid_jira_issue_url(url: str | None, issue_key: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    match = JIRA_ISSUE_PATH.fullmatch(parsed.path)
    return parsed.scheme == "https" and match is not None and match.group(1) == issue_key


def evaluate_response(
    case: EvaluationCase, response: QueryResponse, duration_ms: int
) -> list[CheckResult]:
    answer_folded = response.answer.casefold()
    trace_names = [step.name for step in response.trace]
    checks = [
        _check(
            "retrieval_grade",
            response.retrieval_grade == case.expected_grade,
            f"expected {case.expected_grade}, got {response.retrieval_grade}",
        ),
        _check(
            "citation_count",
            len(response.citations) == case.expected_citations,
            f"expected {case.expected_citations}, got {len(response.citations)}",
        ),
        _check(
            "tool_route",
            all(tool in response.tools_used for tool in case.required_tools),
            f"required {case.required_tools}; got {response.tools_used}",
        ),
        _check(
            "trace_contract",
            all(step in trace_names for step in case.required_trace_steps),
            f"required {case.required_trace_steps}; got {trace_names}",
        ),
        _check(
            "gap_disclosure",
            bool(response.unresolved_gaps) is case.expect_unresolved_gap,
            (
                f"expected gap={case.expect_unresolved_gap}; "
                f"got {len(response.unresolved_gaps)} gap(s)"
            ),
        ),
        _check(
            "latency_budget",
            duration_ms <= case.max_latency_ms,
            f"budget {case.max_latency_ms}ms; got {duration_ms}ms",
        ),
    ]

    for phrase in case.answer_contains:
        checks.append(
            _check(
                f"answer_contains:{phrase}",
                phrase.casefold() in answer_folded,
                f"expected answer to contain {phrase!r}",
            )
        )

    if case.expected_outcome == "found":
        citation = response.citations[0] if len(response.citations) == 1 else None
        evidence = response.evidence[0] if len(response.evidence) == 1 else None
        if case.source_type == "github":
            expected_sha = case.expected_sha or ""
            checks.extend(
                [
                    _check(
                        "sha_in_answer",
                        expected_sha[:7] in response.answer,
                        f"expected short SHA {expected_sha[:7]} in answer",
                    ),
                    _check(
                        "citation_sha",
                        citation is not None
                        and citation.url is not None
                        and citation.url.endswith(f"/commit/{expected_sha}"),
                        f"expected citation URL for {expected_sha}",
                    ),
                    _check(
                        "citation_url_shape",
                        citation is not None and _valid_github_commit_url(citation.url),
                        "citation must be an HTTPS GitHub commit URL with a full SHA",
                    ),
                ]
            )
        else:
            expected_issue_key = case.expected_issue_key or ""
            checks.extend(
                [
                    _check(
                        "issue_key_in_answer",
                        expected_issue_key in response.answer,
                        f"expected issue key {expected_issue_key} in answer",
                    ),
                    _check(
                        "citation_issue_key",
                        citation is not None
                        and citation.url is not None
                        and citation.url.endswith(f"/browse/{expected_issue_key}"),
                        f"expected citation URL for {expected_issue_key}",
                    ),
                    _check(
                        "citation_url_shape",
                        citation is not None
                        and _valid_jira_issue_url(citation.url, expected_issue_key),
                        "citation must be an HTTPS Jira browse URL with the expected issue key",
                    ),
                ]
            )
        checks.append(
            _check(
                "evidence_cardinality",
                evidence is not None,
                f"expected one evidence item, got {len(response.evidence)}",
            )
        )
        if case.expected_author:
            checks.append(
                _check(
                    "author_identity",
                    case.expected_author.casefold() in answer_folded,
                    f"expected resolved author {case.expected_author!r}",
                )
            )
        if case.expected_title:
            checks.append(
                _check(
                    "citation_title",
                    citation is not None and citation.title == case.expected_title,
                    f"expected citation title {case.expected_title!r}",
                )
            )
    else:
        checks.extend(
            [
                _check(
                    "no_unsupported_citation",
                    not response.citations,
                    "non-found outcomes must not emit citations",
                ),
                _check(
                    "no_unsupported_evidence",
                    not response.evidence,
                    "non-found outcomes must not emit evidence",
                ),
            ]
        )

    return checks
