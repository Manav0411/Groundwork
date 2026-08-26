"""Turn-level checks, split by who decides the outcome.

The split is the design. `evaluate_hard` covers properties the code determines — if one of these
fails it is a bug, every time, and it gates. `evaluate_measured` covers properties a 3B model
determines; those are run repeatedly and reported as a rate, because asserting them once turns
model variance into a red build and a gate nobody trusts.
"""

from app.models.schemas import QueryResponse
from app.services.citations import CITATION_MARKER
from evals.conversation_models import TurnExpectation


def evaluate_hard(expectation: TurnExpectation, response: QueryResponse) -> list[str]:
    """Return a failure description per violated hard expectation; empty means pass."""
    failures: list[str] = []

    if expectation.expect_route is not None and response.query_type != expectation.expect_route:
        failures.append(
            f"route: expected {expectation.expect_route!r}, got {response.query_type!r}"
        )

    expected_grade = expectation.expect_grade
    if expected_grade is not None and response.retrieval_grade != expected_grade:
        failures.append(
            f"grade: expected {expectation.expect_grade}, got {response.retrieval_grade}"
        )

    count = len(response.citations)
    expected = expectation.expect_citations
    if expected == "none" and count:
        failures.append(f"citations: expected none, got {count}")
    elif expected == "some" and not count:
        failures.append("citations: expected at least one, got none")
    elif isinstance(expected, int) and count != expected:
        failures.append(f"citations: expected {expected}, got {count}")

    if expectation.expect_gap is not None:
        has_gap = bool(response.unresolved_gaps)
        if has_gap is not expectation.expect_gap:
            failures.append(f"gap: expected {expectation.expect_gap}, got {has_gap}")

    # Every marker in the answer must resolve to an emitted citation. This is a project invariant
    # rather than a per-case expectation, so it is checked on every turn without being declared.
    claimed = {int(match.group(1)) for match in CITATION_MARKER.finditer(response.answer)}
    available = {citation.id for citation in response.citations}
    if claimed - available:
        failures.append(
            f"unresolved citation marker(s) {sorted(claimed - available)}; "
            f"emitted {sorted(available)}"
        )

    answer_folded = response.answer.casefold()
    for phrase in expectation.answer_excludes:
        if phrase.casefold() in answer_folded:
            failures.append(f"answer must not contain {phrase!r}")

    return failures


def evaluate_measured(expectation: TurnExpectation, response: QueryResponse) -> dict[str, bool]:
    """Return a pass/fail per model-dependent expectation, to be averaged over trials."""
    results: dict[str, bool] = {}

    if expectation.expect_resolved is not None:
        results["resolved"] = (response.resolved_query is not None) is expectation.expect_resolved

    resolved_folded = (response.resolved_query or "").casefold()
    for phrase in expectation.resolved_contains:
        results[f"resolved_contains:{phrase}"] = phrase.casefold() in resolved_folded

    answer_folded = response.answer.casefold()
    for phrase in expectation.answer_contains:
        results[f"answer_contains:{phrase}"] = phrase.casefold() in answer_folded

    return results
