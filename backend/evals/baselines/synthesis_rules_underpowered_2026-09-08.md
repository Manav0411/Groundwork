# The two grounding rules, measured twice, and why the experiment stops here

The rules against invented causation and merged identities were re-applied on 2026-09-08 without
the citation-count rule that caused the dropout on 09-07, deployed, and measured over twelve
answers. They are reverted again, and this time the reason is not a defect in the change. It is
that this experiment cannot be run at the sample size it needs.

## The numbers

Two questions reach the writer on this corpus. Three trials each, per sample.

| | Old prompt (09-07) | Two rules, sample A | Two rules, sample B | Two rules, combined |
|---|---:|---:|---:|---:|
| Answers | 6 | 6 | 6 | 12 |
| Flagged | 3 | **0** | **4** | 4 |
| Zero-citation | 0 | 0 | 0 | 0 |
| Graded `correct` | 3 | 6 | 2 | 8 |

Old prompt 3/6 against the two-rule 4/12: **p = 0.63**. No detectable effect.

## The finding that matters

Sample A and sample B are the **same prompt, same corpus, same command**, twenty minutes apart.
0 of 6 flagged, then 4 of 6. Fisher gives **p = 0.061** between two samples of one arm.

The between-sample noise is as large as any effect being looked for. That makes every
prompt comparison run at this size uninterpretable, including the 3 to 1 improvement recorded
yesterday, which now reads as the same noise pointing the other way.

Had sample B not been run, this document would be reporting a fix: 0 flags out of 6, the targeted
failure apparently eliminated. The seventh live query was what disproved it, and it was only asked
because six looked too good.

## What the flagged claims still say

Unchanged, which is the second reason not to keep the rules:

- *"The grader model was chosen **because** recall was the decisive metric..."*
- *"Manav Goel is the person working on the project, **as shown by** the Jira issue assignments and
  the commit author information"*

The first is the exact sentence pattern the first rule forbids; the second is the identity merge the
second rule forbids. Both rules were in the deployed prompt when these were written.

## Why it stops

To separate a flag rate of 50% from one of 25% at conventional power needs roughly 50 answers per
arm. At about 6k tokens per answer on the grader-class model, one arm is ~300k tokens against a
200k daily ceiling: two arms cannot be run in a day, let alone repeated.

So the honest position is not "the rules do not work". It is that **prompt effects of this size
cannot be measured on this budget**, and shipping wording on the strength of six runs is how the
09-07 result happened. The rules stay reverted, the measurement stays recorded, and the next person
who wants to tune this prompt should first read the noise floor above.

Options, if it is ever worth revisiting: batch many questions into one grading call to cut the
per-answer cost, judge writer output offline against fixed evidence so the grader is not paid for
at all, or accept a paid tier.
