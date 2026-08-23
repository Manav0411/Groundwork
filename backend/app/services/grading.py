"""CRAG-style retrieval grading.

Phase 2 established, with measurements, that no distance threshold can decide whether retrieved
evidence actually answers a question on this corpus: genuinely relevant paraphrases scored 0.185
cosine while deliberately unanswerable questions scored up to 0.297. Separating those requires a
model that reads the question and the evidence together, which is what this module does.

The grader makes one batched call for the whole evidence set rather than one call per chunk. On CPU
that is the difference between a usable corrective loop and an unusable one, and relevance
judgement is easy enough that a ~1B model handles it.
"""

from dataclasses import dataclass

from app.core.config import settings
from app.models.schemas import RetrievalGrade
from app.services.llm import OllamaClient
from app.services.retrieval import RetrievedRecord

SYSTEM_PROMPT = """Decide whether search results contain the information a question asks for.
Fill the JSON in order:
1. "needed": name the specific fact the question asks for.
2. "evidence": copy the exact phrase from the results that states that fact, or NONE.
3. "answerable": true only if you copied a real phrase into "evidence".
Results often concern related technology, or share words with the question, without
answering it. NONE is a common and correct outcome; do not strain to find a connection.
Reply with JSON only:
{"needed":"<fact>","evidence":"<phrase or NONE>","answerable":true|false}"""

MAX_SNIPPET_CHARS = 400


@dataclass(frozen=True)
class SufficiencyVerdict:
    """What the grader concluded, kept for the trace so the judgement stays auditable."""

    answerable: bool
    needed: str
    evidence: str


@dataclass(frozen=True)
class GradeResult:
    grade: RetrievalGrade
    kept: list[RetrievedRecord]
    verdict: SufficiencyVerdict | None = None
    summary: str = ""
    used_model: bool = False

    @property
    def is_sufficient(self) -> bool:
        return bool(self.kept)


def build_grading_prompt(query: str, records: list[RetrievedRecord]) -> tuple[str, str]:
    lines = [
        f"{index}. [{record.source_type}] {record.title} — "
        f"{record.content[:MAX_SNIPPET_CHARS]}"
        for index, record in enumerate(records, start=1)
    ]
    user_prompt = (
        f"Question: {query}\n\nEvidence:\n"
        + "\n".join(lines)
        + f"\n\nJudge all {len(records)} items."
    )
    return SYSTEM_PROMPT, user_prompt


def parse_verdict(payload: dict) -> SufficiencyVerdict:
    """Read the sufficiency decision, trusting the copied phrase over the boolean.

    Small models set `answerable` true almost reflexively: one measured run returned
    `answerable: true` alongside the reason "Specify Python 3.11 is not a payment gateway
    integration issue". Requiring a copied phrase, and treating its absence as decisive, is far
    more reliable than the flag on its own.
    """
    if "answerable" not in payload and "evidence" not in payload:
        raise ValueError("response contained no sufficiency decision")
    evidence = str(payload.get("evidence") or "").strip()
    answerable = bool(payload.get("answerable"))
    if not evidence or evidence.upper().strip(" .\"'") == "NONE":
        answerable = False
    return SufficiencyVerdict(
        answerable=answerable,
        needed=str(payload.get("needed") or "").strip()[:200],
        evidence=evidence[:300],
    )


def _derived_grade(records: list[RetrievedRecord], reason: str) -> GradeResult:
    """Fall back to the Phase 1 derived grade, and say so rather than degrading silently."""
    if not records:
        return GradeResult(
            grade="incorrect",
            kept=[],
            summary=f"No evidence was retrieved; graded incorrect. ({reason})",
        )
    lexical_hits = sum(1 for record in records if record.lexical_score > 0)
    return GradeResult(
        grade="ambiguous",
        kept=list(records),
        summary=(
            f"Model grading unavailable, so relevance was not verified: kept all "
            f"{len(records)} retrieved chunk(s) ({lexical_hits} matched lexically) and downgraded "
            f"the grade. ({reason})"
        ),
    )


async def grade_retrieval(
    query: str,
    records: list[RetrievedRecord],
    *,
    ollama: OllamaClient | None = None,
    corrected: bool = False,
) -> GradeResult:
    """Judge which retrieved chunks actually support an answer to `query`.

    `corrected` marks that a corrective attempt was needed to get here, which caps the grade at
    `ambiguous`: the answer is supported, but not by what the first retrieval returned.
    """
    if not records:
        return GradeResult(
            grade="incorrect", kept=[], summary="No evidence was retrieved; graded incorrect."
        )
    if not settings.grader_enabled:
        return _derived_grade(records, "grader disabled by configuration")

    client = ollama or OllamaClient()
    system_prompt, user_prompt = build_grading_prompt(query, records)
    try:
        payload = await client.generate_json(
            system_prompt,
            user_prompt,
            model=settings.grader_model,
            timeout_seconds=settings.grader_timeout_seconds,
        )
        verdict = parse_verdict(payload)
    except Exception as exc:
        if not settings.llm_fallback_enabled:
            raise
        return _derived_grade(records, f"{type(exc).__name__}: {exc}"[:160])

    if not verdict.answerable:
        return GradeResult(
            grade="incorrect",
            kept=[],
            verdict=verdict,
            summary=(
                f"Graded the {len(records)} retrieved chunk(s) insufficient: no passage states "
                f"{verdict.needed or 'the requested information'}."
            ),
            used_model=True,
        )

    grade: RetrievalGrade = "ambiguous" if corrected else "correct"
    qualifier = " after corrective retrieval" if corrected else ""
    return GradeResult(
        grade=grade,
        kept=list(records),
        verdict=verdict,
        summary=(
            f"Graded {len(records)} retrieved chunk(s) sufficient{qualifier}; supporting "
            f"passage: {verdict.evidence[:120]!r}."
        ),
        used_model=True,
    )

REWRITE_SYSTEM_PROMPT = (
    "You rewrite an engineering question so a keyword-and-embedding search over commit messages "
    "and issue trackers is more likely to match it. Keep the meaning identical. Prefer concrete "
    "technical nouns over conversational phrasing. "
    'Reply with JSON only: {"query": "<rewritten question>"}'
)


async def rewrite_query(query: str, ollama: OllamaClient | None = None) -> str | None:
    """Restate a question in terms closer to how the corpus is written.

    Worth doing only because Phase 2 revived the lexical retriever: while the tsquery ANDed every
    term, retrieval was nearly query-independent and rewriting changed nothing. Returns None when
    the model is unavailable or gives back something unusable, so the caller can skip the attempt
    rather than retry with a degraded query.
    """
    client = ollama or OllamaClient()
    try:
        payload = await client.generate_json(
            REWRITE_SYSTEM_PROMPT,
            f"Question: {query}",
            model=settings.grader_model,
            timeout_seconds=settings.grader_timeout_seconds,
        )
    except Exception:
        return None
    rewritten = str(payload.get("query") or "").strip()
    if not rewritten or rewritten.casefold() == query.casefold() or len(rewritten) > 500:
        return None
    return rewritten
