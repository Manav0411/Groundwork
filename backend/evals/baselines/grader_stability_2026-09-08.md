# Is the grader deterministic? Yes, 30 replays out of 30

Written to test a claim I made on 2026-09-07 and got wrong. That day I reported that two questions
were answered with citations in the morning and refused at midday, concluded the grader returns
different verdicts on identical input, and called it more important than the prompt work it
interrupted.

## The measurement

`evals/grader_stability_runner.py` retrieves once per question, freezes the chunk set, then calls
the grader ten times on that identical set. Retrieval is repeated three times first, so retrieval
variance cannot be mistaken for grader variance. Run inside the deployed backend container.

| Question | Retrieval stable | Verdicts |
|---|---|---|
| How does retrieval work? | yes | `incorrect` x10 |
| Why is the backend deployed on EC2? | yes | `incorrect` x10 |
| Why did we delete the synthetic demo evidence? | yes | `correct` x10 |

**Zero flips in 30 replays.** The grader is consistent, and so is the retrieval feeding it.

## What I actually got wrong

The persisted `query_runs` rows settle it. Both questions graded `incorrect` in the morning pass
too, at 05:16-05:21, not only at 06:28-06:33:

    05:16  How does retrieval work?             incorrect
    05:19  Why is the backend deployed on EC2?  incorrect
    06:28  How does retrieval work?             incorrect
    06:31  Why is the backend deployed on EC2?  incorrect

Fifteen rows, fifteen refusals, across both passes. Nothing flipped. The error was mine: the runner
reports `checked: true` for a refusal, because the entailment step runs and honestly says "No cited
claim to check". I read `checked` as `answered`, decided six answers had disappeared from the
population between passes, and invented a confound to explain it.

So the corpus simply does not answer those two questions, consistently, and always has not.

## The prompt comparison, corrected

Because the population never changed, yesterday's before/after **is** comparable. Reconstructed
from `query_runs` joined to `query_citations`, over the six turns that reach the writer:

| | Before | After |
|---|---:|---:|
| Answers with a flagged claim | 3 | **1** |
| Answers graded `correct` | 3 | 2 |
| **Answers with zero citations** | **0** | **3** |
| Largest citation count on one answer | 8 | 3 |

The rules did what they were written to do. Flags fell, and the shotgun citing they targeted
disappeared: the two answers that cited eight lines at once are gone. But three of six answers came
back with no citations at all, and an uncited answer is stripped by the validator and downgraded,
which is a worse outcome than a flagged one.

**So the revert stands, for a better reason than the one I gave.** It is not that the measurement
was uninterpretable. It is that the change traded three flagged answers for three uncited ones.

The likely culprit is the third rule, "Cite the one or two lines the sentence rests on", read
against the hard requirement above it that every factual sentence must carry an id. A retry should
drop that rule, keep the two about invented reasons and merged identities, and measure again.

## What this costs the rest of the repo

Nothing, and that is the useful part. The single-run release gates were suspect only if the grader
was unstable. It is stable, on this corpus, at temperature 0. The gates measure the system.
