"""Citation validation.

The agent previously emitted a "Citation Validator" trace step whose summary was a hardcoded
string; no validation ran. A synthesized answer could cite `[7]` when only two citations existed
and the claim would reach the user unchallenged. This module makes the step real: markers that do
not resolve to an emitted citation are stripped from the answer, the retrieval grade is downgraded,
and the discrepancy is disclosed as an unresolved gap.
"""

import re
from dataclasses import dataclass

from app.models.schemas import Citation, RetrievalGrade

CITATION_MARKER = re.compile(r"\[(\d+)\]")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")
_REPEATED_SPACE = re.compile(r"[ \t]{2,}")


@dataclass(frozen=True)
class CitationValidation:
    answer: str
    """The answer with unsupported markers removed."""

    valid_ordinals: list[int]
    invalid_ordinals: list[int]
    uncited: bool
    """True when citations were available but the answer referenced none of them."""

    grade_override: RetrievalGrade | None
    gaps: list[str]

    @property
    def summary(self) -> str:
        if self.invalid_ordinals:
            removed = ", ".join(f"[{ordinal}]" for ordinal in self.invalid_ordinals)
            return (
                f"Removed {len(self.invalid_ordinals)} unsupported citation marker(s) {removed}; "
                f"{len(self.valid_ordinals)} marker(s) resolved to emitted citations."
            )
        if self.uncited:
            return "Answer cited none of the available citations; downgraded the retrieval grade."
        if self.valid_ordinals:
            return (
                f"Validated {len(self.valid_ordinals)} citation marker(s) "
                "against emitted evidence."
            )
        return "No citation emitted and none claimed."


def _strip_markers(answer: str, ordinals: set[int]) -> str:
    def replace(match: re.Match[str]) -> str:
        return "" if int(match.group(1)) in ordinals else match.group(0)

    cleaned = CITATION_MARKER.sub(replace, answer)
    cleaned = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", cleaned)
    cleaned = _REPEATED_SPACE.sub(" ", cleaned)
    return cleaned.strip()


def validate_citations(answer: str, citations: list[Citation]) -> CitationValidation:
    """Check every `[n]` marker in `answer` against the citations actually emitted."""
    available = {citation.id for citation in citations}
    claimed = {int(match.group(1)) for match in CITATION_MARKER.finditer(answer)}
    valid = sorted(claimed & available)
    invalid = sorted(claimed - available)

    # Only a claim of support that cannot be honoured counts as uncited; an answer with no
    # evidence to cite (a not-found disclosure) is correct to cite nothing.
    uncited = bool(available) and not valid

    gaps: list[str] = []
    grade_override: RetrievalGrade | None = None
    if invalid:
        markers = ", ".join(f"[{ordinal}]" for ordinal in invalid)
        gaps.append(
            f"The generated answer cited {markers}, which does not match any retrieved evidence; "
            "the unsupported reference was removed."
        )
        grade_override = "ambiguous"
    if uncited:
        gaps.append(
            "The generated answer did not cite any of the retrieved evidence, so its claims "
            "could not be traced to a source."
        )
        grade_override = "ambiguous"

    return CitationValidation(
        answer=_strip_markers(answer, set(invalid)) if invalid else answer,
        valid_ordinals=valid,
        invalid_ordinals=invalid,
        uncited=uncited,
        grade_override=grade_override,
        gaps=gaps,
    )
