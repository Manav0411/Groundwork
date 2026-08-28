# Inference runtime baseline — 2026-08-26

Re-measured 2026-08-26 (Phase 8.5) to add Qwen3-4B and prompt throughput.

Ollama moved off Docker and onto the host. Everything else in the stack is unchanged, so this
isolates one variable: whether the GPU is used.

## Why the container was slow

Docker Desktop cannot reach the Apple Silicon GPU. A containerised Ollama serves the same API at
the same URL and returns the same answers — it just runs every token on emulated CPU inside a Linux
VM, capped at 7.75 GB of the machine's 16 GB. Nothing in the application can see the difference,
which is why `scripts/check_local.sh` now asserts throughput rather than assuming it.

## Throughput, llama3.2:3b, Apple M4

| | prompt | generation |
|---|---:|---:|
| Ollama in Docker (CPU) | 38 tok/s | 7.2 tok/s |
| Ollama on the host (Metal) | **362 tok/s** | **40 tok/s** |

Prompt throughput is the more important number here. Grading feeds the model 8–16 retrieved chunks
and gets one bit of signal back, so its cost is dominated by reading the prompt, not writing the
answer — and that is where the gap is 9.5x rather than 5.6x.

## End to end

| | before | after |
|---|---:|---:|
| RAG turn (retrieve → grade → synthesize) | ~40 s | **3–5 s** |
| Grading, mean | 8.5 s | **3.4 s** |
| Grading, max | 15.2 s | **4.7 s** |
| Structured turn (exact SQL) | 4–30 ms | 4–30 ms |

Retrieval quality is unchanged and was re-measured to confirm it: recall@8 0.807, MRR 0.941,
lexical hit rate 0.400 — identical to `slack.md`, as expected since retrieval never calls the chat
model. Grading accuracy is also unchanged at 0.950 with 2/3 refusals. **The speedup was free**: no
answer changed, only how long it took to produce.

## Model selection, re-run now that an 8B model is affordable

`qwen3:8b` was rejected in an earlier phase because 8B at CPU speed was unusable. That constraint is
gone, so the comparison was re-run rather than inherited.

### Grading — the small model wins, decisively

Over the labelled cases in `retrieval_dataset.jsonl`:

| Grader | accuracy | unanswerable refused | paraphrase preserved | recall | mean | max |
|---|---:|---:|---:|---:|---:|---:|
| **llama3.2:3b** | **0.950** | 2/3 | **2/2** | **1.000** | **1.5 s** | **3.1 s** |
| qwen3:4b (Q4_K_M) | 0.800 | **3/3** | 1/2 | 0.717 | 1.6 s | 3.2 s |
| qwen3:8b | 0.900 | **3/3** | 1/2 | — | 15.0 s | 35.8 s |

**Qwen3-4B added 2026-08-26**, at 2.5 GB the obvious candidate for a memory-constrained deployment
box. Measured, it is the worst of the three at this job, and the number that decides it is recall:
**1.000 → 0.717.** It rejects four questions the corpus genuinely answers —

    readme_updates, credentials_paraphrase, connector_work, ec2_blocker_cross

— each with "no passage states…". That is the dangerous direction of error. A grader that keeps junk
is caught by citation validation; a grader that discards good evidence burns corrective attempts and
refuses questions that had an answer, and nothing downstream recovers it.

It does refuse 3/3 unanswerable, where llama3.2:3b refuses 2/3. That is the same trade qwen3:8b
offered and the same verdict: one extra refusal is not worth four false ones. Warm latency is a
wash (1.6 s against 1.5 s), so there is no compensating speed argument either — unlike the 8B,
which at least lost on a dimension that a faster machine could fix.

`qwen3:8b` refuses one more unanswerable question — the property the grader exists for — and gives
back more than it gains elsewhere, at 4.4x the latency. Grading is long-prompt classification, and
the 8B model's prompt throughput is 109 tok/s against 362 tok/s. It is the wrong shape of model for
this job.

### Synthesis — the larger model writes better, and still loses

`qwen3:8b` is the better writer. Asked why local embeddings were dropped, it cited only the
retrieved text; `llama3.2:3b` added *"reduce maintenance burden"*, which appears nowhere in the
evidence. Warm, that costs 4.0 s against 2.6 s, which would be worth paying.

It was rejected anyway, for a reason that only appears end to end: **the two chat models plus the
embedder are ~9.1 GB, and Ollama will not keep that resident on a 16 GB Mac.** Loading one evicts
another even with `OLLAMA_MAX_LOADED_MODELS=3`. Alternating a 3B grader with an 8B writer then pays
a model load on nearly every request:

| Configuration | RAG turn |
|---|---:|
| One shared model (llama3.2:3b) | **3–5 s** |
| Split 3B grader / 8B writer | 18–22 s, of which ~8 s is reloading |

Four times the latency to avoid one ungrounded adjective is the wrong trade, particularly when the
grader and citation validation already guard the failure modes that matter. `OLLAMA_MODEL=qwen3:8b`
remains a one-line change on a machine with enough memory to hold both.

## What this measurement is worth remembering for

Two of the three model decisions here went against "bigger is better", and neither was predictable
from reputation:

- The right model depends on the **shape of the job**. Long prompt, one bit of output — the small
  model is both faster and more accurate.
- A per-job model split is only worth it if every model **stays resident**. Measured per-call, the
  split looked like a 1.4 s tax; measured end to end it was a 15 s one.

## Limitations

- One machine, one chip. The Docker/host gap is macOS-specific: on Linux with the NVIDIA container
  toolkit a containerised Ollama does reach the GPU, which is why the compose service is kept
  behind the `bundled-ollama` profile rather than deleted.
- 16 grading cases and one corpus. Directional, not precise.
- Synthesis quality was compared on a single question. The claim that qwen3:8b is better grounded
  is one observation, not a measurement, and is recorded as such.
