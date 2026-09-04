# Fusion weight re-sweep — 2026-09-04

GW-8. `RRF_K = 10`, `CANDIDATE_DEPTH = 30`, `LEXICAL_WEIGHT = 0.15`, `VECTOR_WEIGHT = 1.0` were
tuned on 16 cases over 44 documents when GitHub was the only source. `README.md` in this directory
says they were "chosen by sweeping `rrf_k x candidate_depth x weights`" — but the grid was never
recorded. This is that record.

**Outcome: no change.** The shipped configuration is not the top row, and the reason it stays is
worth more than the ranking.

## Method

30 configurations: `rrf_k ∈ {10, 30, 60}` × `candidate_depth ∈ {30, 60}` ×
`lexical_weight ∈ {0.0, 0.15, 0.35, 0.5, 1.0}`, with `vector_weight` fixed at 1.0 because only the
ratio between the two affects ordering. `evals/retrieval_sweep.py`, 20 labelled cases, deployed
corpus, `embeddinggemma`.

Queries are embedded once and replayed from a cache: `hybrid_retrieve` recomputes the query vector
on every call and nothing memoises it, so a naive sweep would be 600 embedding round-trips instead
of 20. The sweep also refuses to report unless vector scores actually came back — `hybrid_retrieve`
swallows embedding failures and continues lexical-only, so a run against a stopped Ollama would
otherwise print a full, plausible, meaningless table.

**The harness reproduces the 24 Aug baseline first**, or none of the rest would mean anything:
Recall@k 0.646 / 0.738 / 0.807, Precision@k 0.550 / 0.400 / 0.287, MRR 0.941, negatives 0/3 — all
exact. Lexical hit rate came back 0.406 against 0.400 recorded, a difference of one chunk in 160.

| Configuration | Recall@8 | MRR | Lexical hit rate | Negatives clean |
|---|---:|---:|---:|---:|
| k=10 depth=30 lex=0 vec=1 | 0.816 | 0.971 | 0.375 | 0/3 |
| k=10 depth=60 lex=0 vec=1 | 0.816 | 0.971 | 0.394 | 0/3 |
| k=30 depth=30 lex=0 vec=1 | 0.816 | 0.971 | 0.375 | 0/3 |
| k=30 depth=60 lex=0 vec=1 | 0.816 | 0.971 | 0.394 | 0/3 |
| k=60 depth=30 lex=0 vec=1 | 0.816 | 0.971 | 0.375 | 0/3 |
| k=60 depth=60 lex=0 vec=1 | 0.816 | 0.971 | 0.394 | 0/3 |
| k=10 depth=30 lex=0.35 vec=1 | 0.814 | 0.941 | 0.463 | 0/3 |
| k=10 depth=60 lex=0.35 vec=1 | 0.814 | 0.941 | 0.475 | 0/3 |
| k=30 depth=30 lex=0.15 vec=1 | 0.814 | 0.941 | 0.450 | 0/3 |
| k=30 depth=30 lex=0.35 vec=1 | 0.814 | 0.941 | 0.512 | 0/3 |
| k=30 depth=60 lex=0.15 vec=1 | 0.814 | 0.941 | 0.456 | 0/3 |
| k=30 depth=60 lex=0.35 vec=1 | 0.814 | 0.941 | 0.525 | 0/3 |
| k=60 depth=30 lex=0.15 vec=1 | 0.814 | 0.941 | 0.487 | 0/3 |
| k=60 depth=30 lex=0.35 vec=1 | 0.814 | 0.941 | 0.512 | 0/3 |
| k=60 depth=60 lex=0.15 vec=1 | 0.814 | 0.941 | 0.487 | 0/3 |
| k=10 depth=30 lex=0.5 vec=1 | 0.814 | 0.912 | 0.494 | 0/3 |
| k=30 depth=30 lex=0.5 vec=1 | 0.814 | 0.912 | 0.512 | 0/3 |
| k=60 depth=30 lex=0.5 vec=1 | 0.814 | 0.912 | 0.512 | 0/3 |
| k=10 depth=30 lex=0.15 vec=1 **(shipped)** | 0.807 | 0.941 | 0.406 | 0/3 |
| k=10 depth=60 lex=0.15 vec=1 | 0.807 | 0.941 | 0.419 | 0/3 |
| k=10 depth=60 lex=0.5 vec=1 | 0.797 | 0.912 | 0.525 | 0/3 |
| k=60 depth=60 lex=0.35 vec=1 | 0.797 | 0.912 | 0.550 | 0/3 |
| k=10 depth=30 lex=1 vec=1 | 0.797 | 0.902 | 0.544 | 0/3 |
| k=30 depth=30 lex=1 vec=1 | 0.797 | 0.902 | 0.544 | 0/3 |
| k=60 depth=30 lex=1 vec=1 | 0.797 | 0.902 | 0.544 | 0/3 |
| k=30 depth=60 lex=0.5 vec=1 | 0.797 | 0.882 | 0.550 | 0/3 |
| k=60 depth=60 lex=0.5 vec=1 | 0.797 | 0.873 | 0.550 | 0/3 |
| k=10 depth=60 lex=1 vec=1 | 0.797 | 0.863 | 0.550 | 0/3 |
| k=30 depth=60 lex=1 vec=1 | 0.797 | 0.863 | 0.550 | 0/3 |
| k=60 depth=60 lex=1 vec=1 | 0.797 | 0.863 | 0.550 | 0/3 |

## Why the top row is not adopted

Vector-only (`lex=0`) leads on both decision metrics — Recall@8 0.816 against 0.807, MRR 0.971
against 0.941 — and the shipped configuration ranks 19th of 30. Taken at face value that is a
change. Read per case, it is not:

| Case | Recall@8 shipped | Recall@8 vector-only | RR shipped | RR vector-only |
|---|---:|---:|---:|---:|
| `vercel_routing` | 0.667 | **1.000** | 0.50 | 0.50 |
| `startup_blocking` | 0.714 | **0.571** | 1.00 | 1.00 |
| `embeddings_rationale` | 1.000 | 1.000 | 0.50 | **1.00** |

**Three of twenty cases differ, and two of them move in opposite directions.** The +0.009 Recall@8
is one case gained and one case lost; the MRR gain is a single case's reciprocal rank going 0.5 to
1.0. A mean over 20 cases cannot separate that from noise, and the aggregate actively hides that
lexical weighting is what rescues `startup_blocking`.

Dropping lexical to zero would also discard a capability this dataset does not test. Lexical
retrieval is what matches rare tokens exactly — commit SHAs, issue keys, error strings. The
baseline in `README.md` records the tsquery fix taking lexical hit rate from 0.031 to 0.422 and
treats it as a real improvement. Twenty semantic questions are not evidence for undoing that.

## What the sweep does establish

- **Fusion weight barely matters on this corpus.** Every configuration lands within 0.019 Recall@8
  and 0.108 MRR of every other. Vector retrieval dominates, which agrees with the Phase 2 finding
  that vector search alone scored MRR 1.000.
- **`rrf_k` and `candidate_depth` are inert here.** All six `lex=0` rows are identical on recall and
  MRR — expected, since with no lexical contribution `rrf_k` is a monotonic transform of a single
  rank list. Even with lexical in play they move nothing outside the same 1–2 case band. The
  choice of 10 over the paper's 60 is not load-bearing at this corpus size.
- **Higher lexical weight monotonically costs MRR**: 0.941 at 0.15 and 0.35, 0.912 at 0.5, 0.863 at
  1.0. This reproduces the original finding that equal weighting cost MRR, and extends it — the
  penalty is smooth, not a cliff.

## Two weaknesses in the dataset, found by running it 30 times

**Negatives discriminate nothing.** All three negative cases return 8 chunks in all 30
configurations — 0/3 "returning nothing", every time. Retrieval always returns candidates for an
out-of-corpus question; refusing is the grader's job. So the negative cases measure nothing about
fusion, and the column is noise in every fusion report that has ever printed it.

**The corpus is not really three-source.** All 20 cases target `askbase`, whose Slack side is 4
threads across 2 channels. `slack.md` already said a re-sweep is warranted "once the Slack corpus is
large enough for the result to mean anything" and called the data "directional, not precise". That
caveat still holds, and it applies to this document: this is a sweep over a GitHub-and-Jira corpus
with a little Slack in it, not a verdict on three-source fusion.

## Conclusion

The constants are unchanged. The ticket expected this to confirm rather than change, and it does —
but for a better reason than "the current values won". They did not win. They are inside a band the
dataset cannot resolve, and the one configuration that scores higher does so by trading one case for
another while giving up a capability the dataset does not exercise.

Re-run when the Slack corpus is large enough to carry its own cases, and add negative cases that
can actually fail before trusting that column.
