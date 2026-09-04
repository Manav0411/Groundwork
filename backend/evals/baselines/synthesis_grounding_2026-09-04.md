# Grounding the synthesis prompt — 2026-09-04

The production entailment baseline measured a 35% flag rate and adjudicated all seven flags as real.
They shared one shape: the writer adding something its evidence did not say. "Answer only from the
supplied evidence" was already in the prompt and prevented none of it, because the writer believes
it is complying — every sentence really is loosely derived from a passage.

So the requirement became rules naming the four ways it actually failed, the same move that fixed
the citation format: a requirement that is true but buried in prose does not bind.

## Result: directionally right, and smaller than the headline

| | before | after (2 trials) | after (5 trials) |
|---|---:|---:|---:|
| Answers with claims to judge | 14 | 15 | 18 |
| **Flagged** | 6 (**43%**) | 4 (**27%**) | 5 (**28%**) |
| Graded `correct` | 6 of 20 | 9 of 20 | 12 of 50 |

The two runs after the change agree closely — 27% and 28% on independent samples — which is more
convincing than either alone.

**These rates are corrected.** The runner originally divided flagged answers by every answer it had
"checked", including refusals that had no claims to judge at all. That reported 35%, 25% and 16%
for the three runs, diluted by however many refusals each happened to contain. It is the same defect
the retrieval report carried, where negative cases scoring 0 by construction were averaged into
recall — found and fixed the same day, then reintroduced in a new harness. The denominator is now
answers that actually had a claim.

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

## Cost, and why the longer run is not simply better

**19 of 50 answers went unchecked on the 5-trial run** — the provider's per-minute ceiling — despite
25 seconds of pacing between requests. Pacing is client-side; the ceiling is tokens per minute, and
synthesis, grading and entailment compete for it. Waiting longer between questions does not help
when a single question spends three calls.

So the larger sample bought less than its size suggests: 50 answers yielded 18 judgements, against
15 from a run a third the length. That is the binding constraint on measuring this deployment, and
it is worth knowing before anyone plans a bigger run expecting proportionally more signal.
