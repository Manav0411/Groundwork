# Retrieval baseline — 2026-08-22

Measured with `evals/retrieval_runner.py` over `evals/retrieval_dataset.jsonl` (16 labelled cases)
against the live AskBase index: 44 documents (36 GitHub commits, 8 Jira issues), all embedded with
`embeddinggemma`.

Run it inside the backend container — a separate host PostgreSQL commonly owns `localhost:5432` and
shadows the published port:

```bash
docker compose run --rm --no-deps backend python -m evals.retrieval_runner
```

## Before and after

| Metric | Before | After |
|---|---:|---:|
| Recall@5 | 0.771 | 0.771 |
| Recall@8 | 0.792 | 0.792 |
| Precision@8 | 0.281 | 0.281 |
| MRR | 1.000 | 1.000 |
| **Lexical hit rate** | **0.031** | **0.422** |
| Negative cases returning nothing | 0/3 | 0/3 |

## What this says

**The lexical retriever was dead and now works.** `websearch_to_tsquery` ANDs every term, so a
natural-language question required one chunk to contain all of its stemmed terms. Only 3% of
returned chunks had any lexical match; now 42% do. This also silently repaired the documented
"degrades to full-text search when embeddings are unavailable" contract, which could not have held
under AND semantics — with Ollama stopped, a question that previously matched almost nothing now
returns 8 lexically-matched results.

**Ranking quality did not improve, because it was already optimal.** Vector search alone scored
MRR 1.000 — a relevant document at rank 1 on every positive case. Every fusion configuration that
let lexical rank drive the ordering *lost* recall and MRR (equal weighting cost 0.115 MRR). The
shipped weighting (`LEXICAL_WEIGHT = 0.15`) makes lexical a tie-breaker rather than a driver, chosen
by sweeping `rrf_k × candidate_depth × weights` and taking the configuration with the best MRR and
recall at the deepest candidate pool. It is tuned on 16 cases over 44 short commit messages, which
is close to the worst case for `ts_rank_cd`, and should be re-swept as the corpus grows.

**The architectural defect is fixed regardless of the metrics.** The candidate filter previously
read `search_vector @@ query OR embedding IS NOT NULL`, admitting every embedded chunk in the
project — a top-k over the whole corpus rather than a search. Both retrievers are now bounded to
`CANDIDATE_DEPTH` and fused with reciprocal rank fusion, and results are collapsed to one chunk per
document.

## Known limitation: no-answer detection is not solvable at this layer

All three negative cases still return 8 chunks. This was the one planned improvement that the
measurement ruled out, and the reason is worth recording.

Top cosine similarity, relevant documents versus deliberately unanswerable questions:

| Case | Category | Top similarity |
|---|---|---:|
| `credentials_paraphrase` | relevant | 0.185 |
| `slow_boot_paraphrase` | relevant | 0.210 |
| `sprint_velocity_negative` | **unanswerable** | 0.209 |
| `kubernetes_negative` | **unanswerable** | 0.279 |
| `payment_gateway_negative` | **unanswerable** | 0.297 |

The paraphrase cases — precisely what vector search exists to handle — score *lower* than the
unanswerable ones. A floor at 0.30 removes the negatives and destroys both paraphrase cases; a floor
at 0.17 preserves paraphrases and admits every negative. No global cosine threshold separates them,
and a within-query relative cutoff fares no better: the negatives' score distributions are as flat
as the paraphrases'.

Deciding whether retrieved evidence is *sufficient* needs more than a distance metric. That belongs
to the retrieval grader in the next phase, where an LLM can judge the evidence against the question,
rather than being forced into a SQL predicate here.
