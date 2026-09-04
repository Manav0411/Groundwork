"""Does the claim actually say what the passage it cites says?

Citation validation checks that every `[n]` resolves to a citation that was emitted. It never reads
a character of evidence, so an answer can cite a real passage and misstate it and pass cleanly. The
failure is recorded, not theoretical: `baselines/hosted_inference.md` measured an attribution
correct in 0 of 3 runs on the small model and 1 of 3 on the large one, with the citation resolving
correctly every time -- "0.950 vs 0.950", comparing a number to itself, while calling the other
model more accurate.

That was written up as structural: a bigger writer lowers the rate and does not remove the class,
"because the check does not exist". This is the check. It is the same move Phase 2 made one layer
down, where no cosine threshold could separate unanswerable questions from paraphrases and the job
went to a model that reads question and evidence together.

Two choices worth stating, both taken from `grading.py` because it solved the same problem:

**One batched call for the whole answer**, not one per claim. A typical RAG turn is two model calls;
per-claim checking makes it 2+N, batched makes it 3 whatever N is.

**The JSON commits to a copied phrase before it commits to a verdict, and the phrase is trusted over
the verdict.** Small models set a boolean true reflexively -- the grader measured one returning
`answerable: true` beside a reason that contradicted it. Requiring the model to quote the words that
support the claim, and treating an absent quote as decisive, is far more reliable than the flag.
"""

from dataclasses import dataclass

from app.core.config import settings
from app.services.citations import ClaimSpan
from app.services.llm import ChatClient, chat_client

SYSTEM_PROMPT = """Decide whether each claim is supported by the evidence it cites.
For every claim, fill the JSON in order:
1. "quote": copy the exact words from that claim's evidence that state what the claim says, or NONE.
2. "supported": true only if you copied real words into "quote".
A claim is supported only if the evidence states it. Evidence that is merely on the same topic,
or that states something similar but different -- a different number, a reversed comparison, an
unstated cause -- is NOT support. NONE is a common and correct answer.
Paraphrasing is fine: the claim need not use the evidence's wording, only its meaning.
A claim listing several pieces of evidence is supported when they state it between them. No single
piece has to state all of it: quote from whichever one you checked last.
Reply with JSON only:
{"claims":[{"id":<n>,"quote":"<words or NONE>","supported":true|false}]}"""

MAX_PREMISE_CHARS = 400
MAX_CLAIM_CHARS = 400


@dataclass(frozen=True)
class ClaimVerdict:
    """One claim, and whether the evidence it cites was found to state it."""

    index: int
    supported: bool
    quote: str
    text: str
    ordinals: list[int]


@dataclass(frozen=True)
class EntailmentResult:
    verdicts: list[ClaimVerdict]
    summary: str
    """The trace sentence, built here so the node only has to record it."""

    used_model: bool
    """False when the check degraded. Evals need to tell a judgement from an outage."""

    @property
    def unsupported(self) -> list[ClaimVerdict]:
        return [verdict for verdict in self.verdicts if not verdict.supported]


def build_entailment_prompt(
    spans: list[ClaimSpan], premises: dict[int, str]
) -> tuple[str, str]:
    blocks = []
    for index, span in enumerate(spans, start=1):
        cited = "\n".join(
            f"  [{ordinal}] {premises.get(ordinal, '(missing)')[:MAX_PREMISE_CHARS]}"
            for ordinal in span.ordinals
        )
        blocks.append(f"Claim {index}: {span.text[:MAX_CLAIM_CHARS]}\nIts evidence:\n{cited}")
    user_prompt = (
        "\n\n".join(blocks) + f"\n\nJudge all {len(spans)} claim(s), in order."
    )
    return SYSTEM_PROMPT, user_prompt


def parse_entailment(payload: dict, spans: list[ClaimSpan]) -> list[ClaimVerdict]:
    """Read the per-claim decisions, trusting the copied quote over the boolean.

    A claim the model did not return a decision for is treated as supported rather than as a
    failure. Silence is not evidence of a fabrication, and the cost of the two errors is not
    symmetric: wrongly flagging a correct claim downgrades a good answer and teaches the reader to
    ignore the grade.
    """
    if "claims" not in payload:
        raise ValueError("response contained no claim decisions")
    by_id: dict[int, dict] = {}
    for item in payload["claims"]:
        if isinstance(item, dict) and item.get("id") is not None:
            try:
                by_id[int(item["id"])] = item
            except (TypeError, ValueError):
                continue

    verdicts: list[ClaimVerdict] = []
    for index, span in enumerate(spans, start=1):
        item = by_id.get(index)
        quote = str((item or {}).get("quote") or "").strip()
        supported = bool((item or {}).get("supported")) if item is not None else True
        if item is not None and (not quote or quote.upper().strip(" .\"'") == "NONE"):
            supported = False
        verdicts.append(
            ClaimVerdict(
                index=index,
                supported=supported,
                quote=quote[:300],
                text=span.text,
                ordinals=list(span.ordinals),
            )
        )
    return verdicts


def _unchecked(spans: list[ClaimSpan], reason: str) -> EntailmentResult:
    """Degrade permissively: every claim stands, and the trace says the check did not run.

    A judge outage must never look like a fabrication. Reporting an unsupported claim because the
    provider was rate limited would be a lie of exactly the kind this module exists to prevent.
    """
    return EntailmentResult(
        verdicts=[
            ClaimVerdict(
                index=index,
                supported=True,
                quote="",
                text=span.text,
                ordinals=list(span.ordinals),
            )
            for index, span in enumerate(spans, start=1)
        ],
        summary=f"Entailment not checked: {reason}. Claims are shown as written.",
        used_model=False,
    )


def _summarise(verdicts: list[ClaimVerdict]) -> str:
    unsupported = [verdict for verdict in verdicts if not verdict.supported]
    if not unsupported:
        return f"Checked {len(verdicts)} claim(s) against cited evidence; all supported."
    listed = ", ".join(
        "[" + "][".join(str(o) for o in verdict.ordinals) + "]" for verdict in unsupported
    )
    return (
        f"{len(unsupported)} of {len(verdicts)} claim(s) are not stated by the evidence they "
        f"cite ({listed}); downgraded the retrieval grade."
    )


async def check_entailment(
    answer_spans: list[ClaimSpan],
    premises: dict[int, str],
    client: ChatClient | None = None,
) -> EntailmentResult:
    """Judge every cited claim in one call."""
    if not answer_spans:
        return EntailmentResult(verdicts=[], summary="No cited claim to check.", used_model=False)

    client = client or chat_client("entailment")
    system_prompt, user_prompt = build_entailment_prompt(answer_spans, premises)
    try:
        payload = await client.generate_json(system_prompt, user_prompt)
        verdicts = parse_entailment(payload, answer_spans)
    except Exception as exc:
        if not settings.llm_fallback_enabled:
            raise
        return _unchecked(answer_spans, f"{type(exc).__name__}: {exc}"[:160])

    return EntailmentResult(verdicts=verdicts, summary=_summarise(verdicts), used_model=True)
