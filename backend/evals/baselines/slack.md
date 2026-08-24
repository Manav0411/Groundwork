# Slack connector baseline — 2026-08-24

Live workspace: `Groundwork`, channels `#engineering` and `#decisions`, 4 threads. Measured over
`evals/retrieval_dataset.jsonl`, which grew from 16 to 20 cases.

## Sync contract

| Check | Result |
|---|---|
| First sync | 4 threads → 4 documents, 4 chunks, 4 embedded |
| Immediate incremental sync | overlap cursor used, **0 chunks written, 0 re-embedded** |
| Existing GitHub gate | 16/16, 1.000 |
| Existing Jira gate | 5/5, 1.000 |

## Retrieval and grading

| Metric | Before Slack | After Slack |
|---|---:|---:|
| Recall@5 | 0.771 | 0.738 |
| Recall@8 | 0.792 | **0.807** |
| MRR | 1.000 | 0.941 |
| Lexical hit rate | 0.422 | 0.400 |
| **Grading sufficiency accuracy** | 0.875 | **0.950** |
| Paraphrase questions preserved | 0/2 | **2/2** |
| Unanswerable questions refused | 3/3 | 2/3 |

All four new cases — two decision-rationale, two cross-source — reach recall@8 of 1.00.

## The labels were stale, and that mattered

The first measurement after indexing Slack showed recall@5 falling to 0.673 and MRR to 0.746. Most
of that was not degradation. The gold set was labelled when the corpus held only GitHub and Jira, so
a Slack thread that genuinely answers a question counted as a miss:

- `credentials_paraphrase` — the `#engineering` bcrypt thread answers it directly
- `slow_boot_paraphrase` and `startup_blocking` — the boot thread diagnoses the cause
- `embeddings_provider` — the `#decisions` thread states the motivation
- `ec2_plans` — the EC2 thread states the constraint

Re-labelling those five recovered recall@8 above its pre-Slack value. **Adding a source invalidates
a retrieval gold set**; the labels describe a corpus, not a question, and must be revisited whenever
the corpus changes.

## Genuine over-retrieval, separate from the labels

Slack threads were also returned for `email_validator`, `permissions`, `python_version`, and all
three negatives, where they are not relevant. Thread transcripts are several hundred characters of
varied technical vocabulary; a commit message is one line. In a shared vector space the longer, more
topically diverse document matches a wider range of queries, so it crowds out short precise ones.

This is why recall@5 fell while recall@8 rose: relevant short documents are still retrieved, but
pushed down the ranking. Mixing document lengths across sources skews retrieval, and the fusion
weights in `services/retrieval.py` were tuned on a corpus of short commit messages alone.

## Grading moved in both directions

Sufficiency accuracy rose from 0.875 to 0.950, and the two paraphrase cases the grader previously
rejected now pass — not because the grader improved, but because Slack genuinely contains the
answers it was previously right to say were missing.

One negative regressed. `sprint_velocity_negative` ("What is the Sprint 24 delivery velocity?") is
now graded sufficient, citing *"Deploy times went from ~90s to under 20s"* from the boot thread. The
question remains unanswerable — the corpus has no sprints and no Sprint 24 — but it now contains
timing metrics that superficially resemble a velocity figure. The case is kept as-is rather than
reworded: it is a real example of a corpus growing into a question's vocabulary without growing into
its meaning, which is exactly the failure a sufficiency grader exists to catch and did not.

## Limitations

- 4 threads over 2 channels. Directional, not precise.
- Fusion weights are unchanged since Phase 2 and were calibrated pre-Slack. A re-sweep is warranted
  once the Slack corpus is large enough for the result to mean anything.
- Indexed Slack content is people's words, and there is no PII redaction. Channels are configured
  explicitly by id so the captured scope stays deliberate.
