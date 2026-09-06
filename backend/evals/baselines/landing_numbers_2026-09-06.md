# The landing page's numbers, measured in one run

**Date:** 6 Sep 2026
**Backend:** deployed EC2 instance, all three connectors synced immediately before (0 new documents
— the corpus was already current, so this is a clean index, not a re-ingest).
**Latency basis:** the sum of each run's own trace durations — the number the trace strip on the
card adds up to, so a reader can check a claim against the card that produced it.
**Method:** three warm runs per question, median reported. The first call after the instance wakes is
an order of magnitude slower and is excluded by taking a median, not by discarding data.

Written because the landing page carried figures from three different measurement sessions, which is
how the hero came to claim a 20–40 ms exact path while §02 on the same page listed 3–13 ms. Every
latency on the page now derives from this one table.

## Exact routes — zero model calls

| Question | Route | Runs (ms) | Median | Grade |
|---|---|---|---:|---|
| What was the last commit by Manav0411? | `latest_commit` | 61 · 20 · 21 | **21 ms** | correct |
| What is the status of GW-3? | `jira_issue_status` | 6 · 3 · 2 | **3 ms** | correct |
| What was the last conversation on Slack? | `latest_slack_thread` | 8 · 3 · 4 | **4 ms** | correct |
| Are all the tasks complete? | `jira_project_status` | 8 · 4 · 4 | **4 ms** | correct |
| What was the last feature added in this project? | `recent_activity` | 4 · 3 · 3 | **3 ms** | ambiguous |

Band across all five: **3–21 ms**. That is the figure in the hero.

The 61 ms first call is the cold path — connection setup and query plan, not the route. It is left in
the table rather than dropped, because a median that hides its own outlier is not worth more than the
mean it replaced.

`recent_activity` grades `ambiguous` on every run by design: it answers a narrower question than the
one asked and says so — *"Commit messages record what changed, not which feature it belonged to."*

## Cited path

Measured **on the instance**, so it excludes the public chain (Vercel → Caddy → backend) and is
comparable with `deployment_inference.md`'s 67.9 s and 1.6 s, which were measured the same way.

| Question | Runs (wall, ms) | Median | Grade | Citations |
|---|---|---:|---|---:|
| Why did we delete the synthetic demo evidence? | 1828 · 1732 · 1887 | **1.8 s** | correct | 2 |

Trace sums for the same three runs: 1794 · 1712 · 1866 ms. Node count 8, no corrective cycle — the
grader was satisfied on the first pass. This run is what `frontend/lib/fixtures/recorded-run.ts`
records, so §01 and §02 now describe the same run rather than two different ones.

Derived: the hosted-inference saving becomes **97.3%** (1 − 1.8/67.9), down from the 97.6% the page
claimed at 1.6 s. Same finding, one decimal less flattering, and now recomputed from a current run
rather than carried forward.

## The question the page used to feature

*"Why did we choose the grader model?"* — measured through the public chain: 2012 · 2297 · 2604 ms,
median **2.3 s**, and **`ambiguous` on all three runs**. Entailment flagged a different claim each
time:

- *"…the chosen model offered the best trade-off between accuracy (0.950) and speed…"* — cited [5]
- *"The grader model was selected based on its recall performance…"* — cited [1] [5]
- *"…measurements on the same 20 grading cases confirmed…higher accuracy and acceptable latency…"* — cited [2]

The 0.950 figure is qwen3:8b's score, not the 3B grader's. The writer keeps reaching past what the
retrieved passage supports on this question, and the checker keeps catching it — three for three,
each on a claim the evidence does not state.

So the question was retired from §02 rather than re-timed. Featuring an answer the system itself
grades `ambiguous` as the page's example of the cited path would have been a poor advertisement for
either the answer or the checker. It stays recorded here because it is the strongest live evidence
the entailment check works on prose nobody wrote a test case for.

## Fixed counts, for the same page

| Claim | Value | Command |
|---|---|---|
| Tests, default tier | **404** | `pytest --collect-only` |
| Graph | **15 nodes · 20 edges** | `build_graph().get_graph()`, less `__start__`/`__end__` and their three edges |
