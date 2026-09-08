# Tightening the synthesis prompt: measured, confounded, reverted

> **Corrected on 2026-09-08.** The confound described below is not real. The six "grader refusals"
> that appeared only in the after-run were present in the before-run too, which the persisted
> `query_runs` rows show plainly. I misread the runner's `checked` field, which is true for a
> refusal as well as for an answer. The two passes are comparable, and the corrected comparison
> is in `grader_stability_2026-09-08.md`: flags fell 3 to 1, but three of six answers lost their
> citations entirely, which is why the revert was still right. The grader was also measured and
> is deterministic: 30 replays, zero flips.

Two grounding rules were added to `build_answer_prompt` on 2026-09-07 and reverted the same hour.
The measurement did not show they were wrong. It showed it could not tell, which is a different
result and the reason nothing shipped.

## Why the change was attempted

Yesterday's flags, hand-read, were dominated by manufactured causation: the corpus records that
options were weighed and which one won, the question asks *why*, and the writer writes the
connecting sentence itself.

Today's baseline (clean — **12 of 12 answers checked, none skipped**) reproduced it:

| Question | Flagged |
|---|---:|
| Why did we choose the grader model? | 1/3 |
| Who is working on this project? | 2/3 |
| How does retrieval work? | 0/3 |
| Why is the backend deployed on EC2? | 0/3 |

**3 of 12 answers flagged, 3 graded `correct`.** The grader-model flag was the causation class
verbatim — *"selected **because** recall was the decisive metric"*. The two "who is working" flags
were one claim repeated across trials, asserting that a commit author and a Jira assignee were "the
same individual", cited to six lines at once.

So: a rule against supplying reasons, a rule against merging two records into one identity, and a
rule against citing many lines for one sentence.

## What the after-run showed

Same command, same corpus, prompt changed:

| | Before | After |
|---|---:|---:|
| Answers with a flag | 3 | 1 |
| Answers graded `correct` | 3 | 2 |
| **Answers that never reached the writer** | **0** | **6** |

The flag count fell, and the fall means nothing. Six of the twelve answers were **grader refusals**:
retrieval ran, the grader judged 8 chunks insufficient, the corrective loop rewrote and retried,
widened to 16, and was rejected a third time. The writer was never called, so those six answers
could not carry a flag whatever the prompt said. A metric that improves because half the population
stopped being measured is the diluted-denominator error this project has now made three times.

## What ruled the prompt out as the cause

The revert did not restore them. After reverting and redeploying, both refusing questions still
refused, 2 of 2 — and the backend logs show **zero 429s**, every Groq call 200. The refusals are
genuine grader verdicts on the same corpus that answered the same questions 3/3 this morning.

That is worth stating on its own: `How does retrieval work?` and `Why is the backend deployed on
EC2?` were answered with citations at 05:20 and refused at 12:00, with no change to the index, the
corpus, or the retrieval code. The grader is less stable across hours than a single session
suggests.

## Where it stands

Production is back on the previous prompt (`ec66422` reverts `44723a0`). The two rules are not
disproven and not adopted — they are unmeasured, because the run that was supposed to measure them
answered a different question.

A future attempt needs the confound removed first: hold the retrieved evidence fixed and call the
writer and the checker directly, so grader variance cannot decide how many answers are eligible to
be judged. That is a harness that does not exist yet, and it is the honest prerequisite rather than
a bigger sample of the same design.

Token cost today: ~144k of the 200k daily allowance on the grader-class model, which is why the
third pass this needed did not happen.
