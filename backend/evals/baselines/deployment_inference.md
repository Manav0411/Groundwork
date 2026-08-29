# Deployment inference baseline — 2026-08-29

The question Phase 9 opened with: **how slow is a RAG turn on cloud CPU?** Answered by renting the
hardware rather than reasoning about it. Total cost of the experiment: about four cents.

## What was measured

| | |
|---|---|
| Instance | `m7i-flex.large`, ap-south-1 |
| CPU | 2 vCPU Intel Xeon Platinum 8488C (Sapphire Rapids), 3.2 GHz |
| SIMD | `avx512_vnni`, `avx512_bf16` — the instructions llama.cpp uses for quantized inference |
| Memory | 8 GB |
| Model | `llama3.2:3b`, Ollama 0.33.2, `think: false` |

The instance was chosen by constraint, not preference — see "The Free plan caps instance types"
below.

## Result

Same script, same prompts, run against both machines:

| | grading | synthesis | **one RAG turn** |
|---|---:|---:|---:|
| Mac M4 (Metal) | 4.9 s | 3.2 s | **8.1 s** |
| `m7i-flex.large` (CPU) | 30.0 s | 37.9 s | **67.9 s** |

**8.4x slower.** Raw throughput: 4.1 tok/s generation, ~115 tok/s prompt, against 46 and 288 on
Metal.

**67.9 s is a floor, not an estimate.** The prompt was 677 tokens; a real query carries 16 chunks.
It excludes retrieval, and it excludes the corrective loop, which can add two more grading rounds
plus rewrites. A realistic worst case is three to four minutes.

## Where this sits against the other two measurements

`inference.md` established that Docker-on-Mac CPU was 5.6x slower than Metal. Cloud CPU is slower
than **both**:

| | generation | RAG turn |
|---|---:|---:|
| Mac, Metal | 46 tok/s | 8.1 s |
| Mac, Docker (emulated CPU) | 7.2 tok/s | ~40 s |
| EC2 `m7i-flex.large` | **4.1 tok/s** | **67.9 s** |

Prompt throughput is the one place cloud CPU beats Docker — 115 tok/s against 38 — and AVX-512 VNNI
is presumably why. It is not close to enough. Generation is memory-bandwidth bound and two vCPUs
cannot fix that.

## The Free plan caps instance types, which is not in the marketing

Launching `c7g.xlarge` failed outright:

    InvalidParameterCombination: The specified instance type is not eligible for Free Tier.

The 2025-onward AWS Free plan restricts EC2 to six types, regardless of credit balance:

| Type | vCPU | RAM |
|---|---:|---:|
| t3.micro / t4g.micro | 2 | 1 GB |
| t3.small / t4g.small | 2 | 2 GB |
| c7i-flex.large | 2 | 4 GB |
| **m7i-flex.large** | 2 | **8 GB** |

`m7i-flex.large` is the only one with room for a 3B model plus embeddings plus Postgres, so it was
the whole search space, not a choice. A separate limit also applies: the account's on-demand vCPU
quota was **5**, which blocks anything above four vCPUs until raised.

Upgrading to the Paid plan keeps the credits and lifts the type restriction, but the ceiling that
matters is physical. Even the largest sensible CPU box would land around 15–30 s per turn — better
than 68, still several times slower than a laptop.

## What this decides

**CPU inference is not a viable path to a demoable deployment.** That is the finding, and it is
worth more than a passing number: two of the three inference measurements in this project have now
contradicted the intuitive answer.

It does **not** block deployment, because half the system never calls a model. Exact-answer
questions route structured → validate, skipping grading and synthesis entirely (`graph.py:93`), and
answer in 4–30 ms on any hardware. What needs a different answer is generation.

The chosen direction is a second LLM provider behind the existing `OllamaClient` abstraction, used
only in deployment, with Ollama remaining the local default — which `CLAUDE.md` explicitly permits
and which has the side benefit of testing whether that abstraction is real. It has only ever had one
implementation.

Embeddings are expected to stay local on the instance: one short input per query against a 621 MB
model is a different workload from generation. **Not yet measured** — recorded as an assumption to
check, not a result.

## Limitations

- One instance type, one region, one model. The Free plan left no comparison to make.
- `c7g.2xlarge` was never measured, so "15–30 s" above is extrapolation and labelled as such.
- The RAG-turn figure is two model calls timed back to back, not the full graph. It excludes
  retrieval, database round trips, and HTTP overhead, all of which are small next to 68 seconds.
