# Hosted provider baseline — 2026-08-29

A second chat provider, used only where local inference is not viable. Ollama remains the default
and the only required provider: `docker compose up` still runs everything with no API key.

    LLM_PROVIDER=openai_compat python -m evals.grading_runner

## Why there is a second provider at all

`deployment_inference.md` measured a RAG turn at **67.9s on the largest instance the AWS Free plan
allows, against 8.1s on Metal**. Exact-answer questions never call a model, so only generation
needed to move.

The second reason is worth as much. Before this change, `OllamaClient()` was constructed directly at
**12 call sites** and `settings.llm_provider` was consulted in exactly **one** place — a single `if`
that gated whether synthesis ran at all. Calling that a provider abstraction was an untested claim.
It now has two implementations, a protocol, and a factory.

## Provider and model selection

Groq, over Gemini, for a measurable reason rather than a preference: one RAG query makes up to
**eight** model calls (resolution, three gradings through the corrective loop, two rewrites,
synthesis, citation retry). Gemini's free tier allows ~10 requests per minute, so a single corrected
question would rate-limit. Groq allows 30.

**Both obvious models were retired mid-plan.** `llama-3.1-8b-instant` and `llama-3.3-70b-versatile`
were the natural picks and are gone from the account as of August 2026 — confirmed by listing the
key's models, not by reading a blog. Qwen remains but is preview-only ("may be discontinued with
limited notice"), which rules it out for anything that has to keep working. That left the production
GPT-OSS pair, and it is the reason model ids are configuration rather than literals.

| Role | Model | Why |
|---|---|---|
| Grading | `openai/gpt-oss-20b` | Long-prompt classification returning one bit |
| Synthesis | `openai/gpt-oss-120b` | Lower frequency, and where grounding matters |

Splitting the roles is also a rate-limit decision. Limits are counted **per model**, so each role
gets its own 8,000 tokens-per-minute budget and a corrective loop cannot exhaust the one the answer
still needs. Locally the same split was impossible — `config.py` records that one model had to serve
both because a 3B grader plus an 8B writer plus embeddings exceeded 16GB.

## Grading, measured on the same 20 cases

| | `llama3.2:3b` (local) | `gpt-oss-20b` (Groq) |
|---|---:|---:|
| Sufficiency accuracy | 0.950 | **0.950** |
| Unanswerable refused | 2/3 | **3/3** |
| Paraphrase preserved | **2/2** | 1/2 |
| Verdict precision | 0.319 | **0.336** |
| Verdict recall | **1.000** | 0.935 |
| Latency, unthrottled | 3,270 ms | **234 ms** |

Equal accuracy, better refusal, mildly worse paraphrase recovery, and **14x faster**.

The extra refusal is `sprint_velocity_negative` — a documented known limitation since the Slack
corpus grew timing metrics that superficially resemble a velocity figure. The hosted grader refuses
it. That does **not** close the limitation, because the local default still accepts it; it is a
property of one configuration, recorded as such.

Recall falling from 1.000 to 0.935 is the direction `inference.md` calls dangerous — a grader that
discards good evidence burns corrective attempts and refuses answerable questions. At 0.935 it is
mild, nothing like the 0.717 that disqualified `qwen3:4b`, but it is the number to watch.

**Latency needs a caveat.** The full run reports 4,364 ms mean, which is not model speed: it
includes deliberate waiting on rate limits. Unthrottled, the same call measures 234 ms.

## The rate limit is a real operational property

The first attempt at this measurement graded **8 of 20 cases** and silently degraded the rest. A
grading call carries 8–16 chunks — roughly 3,000 tokens — so the 8,000 tokens-per-minute ceiling
allows about two and a half back-to-back gradings.

Two things followed. The client now retries **once**, on the server's own `retry-after`, and only
when that is under 15 seconds — a free-tier window resets in under a minute, so anything longer is a
daily cap and waiting on it would hang the request. And a 429 is surfaced as an explicit provider
error rather than folded into a generic failure, because otherwise it reaches the user as the
deterministic fallback and reads as a quality regression rather than a quota.

That design decision paid off immediately: the failure was legible on the first run.

For a demo — one person, occasional questions — the limits are not close to binding. For an eval
run of twenty back-to-back gradings they are.

## Synthesis: improved, not fixed

`inference.md` records that `llama3.2:3b` reproduces the *direction* of retrieved evidence reliably
and its *figures* unreliably. Re-run on the same question, three times, with `gpt-oss-120b`:

| | `llama3.2:3b` | `gpt-oss-120b` |
|---|---|---|
| "40 tok/s" read as "40x" | every run | **gone** |
| Invented a memory footprint | yes | **gone** |
| 0.900 / 0.950 attribution correct | 0 of 3 | **1 of 3** |

The egregious fabrications are gone. The subtle one is not: two of three runs said "0.950 vs 0.950",
comparing a number to itself, and one of those also called `qwen3:8b` *more* accurate, which is
backwards.

So a larger model lowers the error rate and does not remove the class. **This confirms the
structural limit rather than fixing it**: citation validation checks that every `[n]` resolves to an
emitted citation, not that the claim is entailed by the passage it points at. No model choice
changes that, because the check does not exist.

## What did not move

Everything on the default provider, re-run after the change:

    generalization askbase 7/7 · groundwork 8/8
    askbase gate 100% · jira gate 100% · conversations 20/20
    unit 252 · integration 90

## Limitations

- One provider, one run of 20 grading cases per model. Directional.
- The synthesis comparison is three runs of one question. It is an observation about a known
  failure, not a measurement of grounding, and is recorded as such.
- Written against the OpenAI chat-completions API and verified **only** against Groq. Other servers
  speaking the same shape should work and are untested.
- Embeddings never moved and cannot: the schema hardcodes `Vector(dim=768)` from `embeddinggemma`,
  so a different embedding model would silently return unrelated chunks rather than fail. There is
  an explicit test for this, because the failure is invisible.
