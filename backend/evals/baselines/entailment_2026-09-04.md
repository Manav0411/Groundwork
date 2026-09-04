# Entailment checking — 2026-09-04

Citation validation was set arithmetic on integers. It never read a character of evidence, so an
answer could cite a real passage and misstate it and pass with grade `correct` and no disclosure.
`hosted_inference.md` measured the consequence: an attribution correct in **0 of 3** runs on
`llama3.2:3b` and **1 of 3** on `gpt-oss-120b`, the citation resolving correctly every time.

That was recorded as structural — a larger writer lowers the rate and does not remove the class,
"because the check does not exist". This is the check.

## It caught one on its first live answer

Not a test fixture. A real question, `"Why did we choose the grader model?"`, against the deployed
corpus:

> "In contrast, llama 3.2 (3 B) achieved 0.950 accuracy with full recall, meeting the strictness
> needed for reliable grading" `[1][2]`

**0.950 is qwen3:8b's score. llama3.2:3b scored 0.900.** The same attribution error
`hosted_inference.md` recorded, produced again, citing real evidence. Before this change the answer
graded `correct` with no gap. Now it grades `ambiguous` and the claim is quoted back.

## Measured

Twenty hand-labelled claim/evidence pairs, eleven unsupported. Seven are failures the real system
produced; `attribution_correct` is the correct twin of `attribution_inverted`, so a checker cannot
pass by guessing.

| | |
|---|---:|
| **Recall on unsupported claims** | **0.909** (10 of 11) |
| **Correct on supported claims** | **1.000** (9 of 9) |
| Precision of a flag | **1.000** |

The two are reported apart and never averaged. Missing a fabrication is the failure this exists to
prevent; flagging a correct claim downgrades a good answer and teaches the reader to ignore the
grade, which is worse than no check at all.

| Case | Labelled | Predicted | | Quote |
|---|---|---|---|---|
| `attribution_inverted` | unsupported | unsupported | ok | llama3.2:3b 0.900, qwen3:8b 0.950 |
| `units_misread` | unsupported | unsupported | ok | Generation throughput: 7.2 tok/s containerised against 40 to |
| `invented_footprint` | unsupported | unsupported | ok | NONE |
| `cause_unstated` | unsupported | unsupported | ok | NONE |
| `comparison_reversed` | unsupported | unsupported | ok | LEXICAL_WEIGHT is 0.15 against VECTOR_WEIGHT 1.0, making lex |
| `scope_widened` | unsupported | unsupported | ok | NONE |
| `number_changed` | unsupported | unsupported | ok | NONE |
| `exact_restatement` | supported | supported | ok | Recall@8 dropped to 0.717 from 1.000. |
| `paraphrased` | supported | supported | ok | Grading is classification with a long prompt and a one-bit a |
| `paraphrased_numeric` | supported | supported | ok | Recall@8 dropped to 0.717 from 1.000. It is dropping chunks  |
| `summarised` | supported | supported | ok | Move grader model to settings; record baseline in evals/base |
| `partial_but_true` | supported | supported | ok | Polling with a ten minute overlap cursor is the deliberate c |
| `multi_source` | supported | supported | ok | We kept llama3.2:3b for grading |
| `hedged_claim` | supported | supported | ok | Vector search alone scored MRR 1.000. Every fusion configura |
| `topic_only` | unsupported | unsupported | ok | NONE |
| `plausible_absent` | unsupported | unsupported | ok | NONE |
| `date_asserted` | unsupported | unsupported | ok | NONE |
| `quantifier_overreach` | unsupported | supported | **MISS** | Exact questions have exactly one right answer, decided by or |
| `identity_unified` | supported | supported | ok | GitHub reports the same person as Manav0411 on 31 commits an |

## The one it misses

`quantifier_overreach` — "All retrieval questions are answered without a model call", cited against
"Exact questions have exactly one right answer… so they never touch a model at all". The quantifier
widens from *exact* questions to *all* questions. The model found a real supporting phrase and
accepted it.

Left unfixed rather than tuned away. Quantifier scope is a genuinely hard inference, one case is not
evidence a prompt change would generalise, and every earlier attempt to fix a single case in this
work made things worse before measurement corrected it.

## Two wrong turns, recorded because the sequence matters

**A change that measured inert, on a wrong diagnosis.** `multi_source` was flagged and I added a
prompt instruction that co-cited passages combine. Nothing moved. The reason was that the case was
**mislabelled**: the claim asserted the grader "was kept at 3B" while its first passage only gave a
rationale for small models and never said 3B. The checker had been right. The instruction was
reverted rather than kept for reading sensibly.

**Then the same change, diagnosed properly, worked.** With the case corrected it still failed, so
the prompt and raw response were dumped instead of guessed at. The model had copied a real quote —
"We kept llama3.2:3b for grading" — and still returned `supported: false`, which is defensible: the
claim is a conjunction and no single passage states both halves. Re-applying the instruction on that
evidence took correct-on-supported from 0.889 to **1.000**.

Worth noting why the parser does not simply trust the quote in that case. It downgrades a `true`
verdict with no quote and never upgrades a `false` one with a quote, because a model that copies a
loosely related phrase while correctly rejecting a claim would otherwise be overridden into
supporting it — trading away exactly the recall on fabrications this exists for.

## Cost

One batched call for the whole answer, so a turn goes from two model calls to three whatever the
claim count. Measured on the live answer above: **808 ms**, metrics confirming
`grader=1, synthesis=1, entailment=1`.

Its own model id (`HOSTED_ENTAILMENT_MODEL`, grader-class). Limits are counted per model and this
runs on every synthesised answer, so sharing the grader's bucket would make a checked answer compete
with the grading that produced it.

Nothing else moved: askbase gate 17/17, generalization 8/8 on groundwork and 7/7 on askbase. The
structured routes are untouched by construction — entailment sits on the `synthesize → validate`
edge, and those routes reach `validate` by their own edges.

## Known limits

- **A claim is the span its marker terminates**, not a sentence. The synthesis prompt asks for a
  marker per factual sentence and real output does not comply — a recorded run ends a two-sentence
  paragraph with one trailing marker. Segmenting by marker is well defined on the text the model
  actually produces; it is also generous, since a paragraph-trailing marker takes the whole
  paragraph as its claim.
- **Judged against the snippet, not the full chunk.** The snippet is what synthesis was shown, so
  this asks whether the writer misread its input rather than conflating that with bad truncation.
- **Quantifier scope**, above.
- **The judge is not independent of the corpus it judges.** It is a different model from the writer,
  which is the important separation, but both are hosted by the same provider.
