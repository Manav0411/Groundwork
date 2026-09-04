# Grounding the synthesis prompt — 2026-09-04

The production entailment baseline measured a 35% flag rate and adjudicated all seven flags as real.
They shared one shape: the writer adding something its evidence did not say. "Answer only from the
supplied evidence" was already in the prompt and prevented none of it, because the writer believes
it is complying — every sentence really is loosely derived from a passage.

So the requirement became rules naming the four ways it actually failed, the same move that fixed
the citation format: a requirement that is true but buried in prose does not bind.

## Result: directionally right, and smaller than the headline

| | before | after |
|---|---:|---:|
| Answers with a flagged claim | 6 of 17 (35%) | 4 of 16 (**25%**) |
| Graded `correct` | 6 of 20 | **9 of 20** |

**That aggregate is two answers, and it is confounded.** Ten questions, two trials each, is not a
sample that can carry a 10-point claim. The per-question view is the honest one:

| Question | before | after | |
|---|---:|---:|---|
| what was the last feature added | **2/2** | **0/2** | the recency rule, cleanest evidence |
| who is working on this project | 1/2 | **0/2** | the uncited-trailing-clause rule |
| what are the known limitations | 1/1 | **0/1** | |
| why did we choose the grader model | 1/2 | 1/2 | unchanged |
| why is the backend deployed on EC2 | 1/1 | 1/2 | unchanged |
| how does retrieval work | **0/0** | **2/2** | **not comparable — see below** |
| the remaining four | 0 | 0 | |

**The one that got worse is the interesting one.** *"How does retrieval work?"* previously returned
no citations at all on both trials and graded `incorrect`. It now answers, cites, and overreaches on
both trials. So the after-run is being charged for a question the before-run never exercised, which
means the real improvement is a little better than 35→25 suggests — and also that this question
moved from saying nothing to saying something unsupported. Whether the prompt caused that or
retrieval variance did, one run cannot say.

The three rules that did land map exactly to the failures they were written from. Recency is the
strongest: flagged on both trials before, clean on both after.

## The remaining flags are still real

All four adjudicated by hand against their cited passages. Zero false positives, again.

The best of them: the answer claimed `hybrid_retrieve` *"can take up to four sources (GitHub, Jira,
Slack and a fourth source)"*, citing GW-8 — whose text reads "hybrid_retrieve already accepts all
four as keyword arguments", meaning the four **fusion parameters**, not data sources. The writer
misread the antecedent and invented a fourth connector. Nothing about that sentence is detectable
from citation resolution; the marker is correct.

The other three are invented rationale — *"therefore provides higher recall…"*, *"Choosing EC2 gives
a concrete, reproducible platform… which is why the deployment was set up"* — cited to passages that
record a decision without giving that reason. The "do not turn a finding into a reason" rule is
aimed at exactly this and did not stop it.

## What this does not show

That the flag rate is 25%. n = 20 answers over 10 questions, with four skipped by the provider's
per-minute ceiling. It shows three named failure modes stopping, one question changing character,
and the checker holding precision at 1.000 across both runs.

Re-measure with more trials before quoting a rate anywhere.

## Cost

Four of twenty answers had entailment skipped on rate limiting, against three before, despite 4s
pacing between requests. The pacing is client-side; the ceiling is tokens per minute, and synthesis,
grading and entailment now compete for it. That is the binding constraint on this deployment, not
the extra call.
