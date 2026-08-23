# Retrieval grading baseline — 2026-08-23

Measured with `evals/grading_runner.py` over the 16 labelled cases in
`evals/retrieval_dataset.jsonl`, against the live AskBase index. Run inside the backend container:

```bash
docker compose run --rm --no-deps backend python -m evals.grading_runner
```

## Result

| Metric | Value |
|---|---:|
| **Sufficiency accuracy** | **0.875** |
| Unanswerable questions correctly refused | 3/3 |
| Answerable questions preserved | 11/13 |
| Latency | 8.5 s mean, 15.2 s max |
| Grader model | `llama3.2:3b` |

The defect this phase targeted is fixed. All three unanswerable questions now return no evidence
and grade `incorrect`; before the grader they returned eight arbitrary chunks graded `correct`.

The two failures are `credentials_paraphrase` and `slow_boot_paraphrase` — questions whose wording
shares no vocabulary with the corpus. **Both are recovered by the corrective loop**, which rewrites
the question and retries: *"How are user credentials kept secure?"* becomes *"Securement of user
authentication data in system architecture"*, retrieval succeeds, and the answer is graded
`ambiguous` because correction was needed. Verified end to end through `/query`.

## Why a strict grader is the right operating point

This is the load-bearing design decision, so the reasoning is recorded rather than assumed.

The two error directions are not symmetric here:

- **Accepting irrelevant evidence** makes the agent answer from junk. It violates the project's
  core contract and nothing downstream can detect it.
- **Rejecting relevant evidence** makes the agent say it cannot answer something it could. It is
  annoying but safe — and the corrective loop is built to recover exactly this case.

Because correction can recover false negatives but nothing recovers false positives, strictness is
the correct bias. That inverts the usual instinct to tune for balanced accuracy.

## What was tried and rejected

Seven configurations were measured before settling. The models available on CPU do not discriminate
relevance well; prompt changes shift the operating point along a tradeoff curve rather than
sharpening it. Every configuration traded negatives against positives roughly one-for-one:

| Prompt | Model | Negatives refused | Positives kept |
|---|---|---:|---:|
| Per-chunk verdicts | `llama3.2:1b` | 0/3 | 1/2 |
| Per-chunk + required quote | `llama3.2:1b` | 0/3 | 2/2 |
| Binary "answerable" | `llama3.2:1b` | 0/3 | 5/5 |
| Binary, inverted polarity | `llama3.2:1b` | 3/3 | 0/5 |
| Binary + evidence quote | `llama3.2:1b` | 0/3 | 5/5 |
| Binary + evidence quote | `llama3.2:3b` | 1/3 | 5/5 |
| Binary + self-verification | `llama3.2:3b` | 3/3 | 1/5 |

Three findings worth keeping:

**`llama3.2:1b` does not judge; it anchors on the prompt's implied polarity.** Asking "is this
answerable?" produced true almost everywhere; asking "is the answer missing?" produced true almost
everywhere. On one negative case it returned `answerable: true` beside the reason *"Specify Python
3.11 is not a payment gateway integration issue"* — the reasoning was right and the flag ignored it.
This is why `parse_verdict` trusts the copied evidence phrase over the boolean.

**Per-chunk grading was abandoned for batched binary grading.** Judging eight chunks simultaneously
is a much harder structured-output task than one sufficiency decision, and it was both less accurate
and far slower — the quote-per-chunk variant reached 40–82 s per query. The shipped grader keeps or
discards the whole set. The per-chunk filtering that CRAG describes is not supported by what these
models can actually do here.

**Self-verification overshoots.** Asking the model to check its own quote against the fact it named
pushed negatives to 3/3 but collapsed positives to 1/5, rejecting evidence it had itself quoted
correctly. The shipped prompt stops at requiring the quote.

## Degradation

With Ollama stopped, grading falls back to the Phase 1 derived grade: evidence is preserved, the
grade is capped at `ambiguous`, and the trace states that relevance was not verified along with the
underlying error. Verified by stopping the container.

## Limitations

- 16 cases over 44 documents. The accuracy figure is a signal, not a precise measurement.
- 8.5 s mean grading latency on CPU, and the corrective loop can invoke the grader three times —
  an unanswerable question takes roughly 17 s end to end. Acceptable for a demo, not for production
  interactive use.
- Recovery of paraphrase questions depends on the rewrite producing better vocabulary, which is
  itself a small-model output and not guaranteed.
