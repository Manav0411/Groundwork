# Groundwork

**Engineering knowledge, with evidence.** A self-hosted agent that answers questions about your
engineering projects from GitHub, Jira and Slack, and cites every claim.

**Live demo → https://groundwork-mauve-two.vercel.app**

> The demo backend runs on an EC2 instance that is stopped between sessions to keep it inside a free
> tier. If questions fail, the box is asleep rather than broken.

It answers two different kinds of question by two different mechanisms, on purpose:

| Question | How | Latency |
|---|---|---|
| *"What was the last commit by Manav0411?"* | Typed SQL over normalized identities | **16 ms**, zero model calls |
| *"What is the status of GW-3?"* | Typed SQL | 2 ms, zero model calls |
| *"What was the last conversation on Slack?"* | Typed SQL over thread recency | 3 ms, zero model calls |
| *"Are all the tasks complete?"* | SQL counting by status category | 4 ms, zero model calls |
| *"Why did we choose the grader model?"* | Hybrid retrieval → grade → cited synthesis | ~1.6 s, 8 model calls |

Latencies are the sum of each run's own trace durations — the number the trace itself adds up to,
so it can be checked — measured against the deployed backend with the index freshly synced, median
of three warm runs. The first call after the instance wakes is an order of magnitude slower.

Exact questions have exactly one right answer, decided by ordering or counting. Routing those
through embedding similarity does not make the system more general, it makes it confidently wrong —
cosine similarity has no concept of `max(commit_time)`. So they never touch a model at all.

The project is **free-first**: Ollama and local open models are the default, PostgreSQL + pgvector is
self-hosted, and no paid API is required to run it.

## What is measured, not asserted

Every number here comes from a harness in `backend/evals/`, with the raw runs in
`backend/evals/baselines/`.

**Inference, the same RAG turn on three machines:**

| | RAG turn |
|---|---:|
| EC2 CPU, local chat model | 67.9 s |
| Development laptop (M4, Metal) | 8.1 s |
| **Deployed** (EC2 + hosted chat, embeddings local) | **1.6 s** |

The deployment is faster than the machine it was built on. That follows only because the
measurement identified *which* part was slow — generation, not embedding, and not the exact-answer
path that makes no model call.

**Generalization.** `evals/generalization_runner.py` derives every expectation from the database at
run time, so it carries no knowledge of any corpus:

    askbase     7/7      the project the system was built against
    groundwork  8/8      a second project, answered with no change to the suite

That distinction matters. Every other dataset here hardcodes its expectations, which guards known
behaviour and cannot demonstrate that the system works on data nobody wrote cases for.

**Retrieval and grading.** Recall@8 0.807, MRR 0.941 on a three-source corpus. The grader scores
0.950 sufficiency accuracy and refuses 2 of 3 deliberately unanswerable questions; the third is a
corpus-vocabulary case recorded rather than hidden.

**Model choice went against intuition twice.** `qwen3:8b` and `qwen3:4b` were both measured and
both rejected — the 4B model's recall fell from 1.000 to 0.717, discarding evidence the corpus
genuinely holds. Grading is long-prompt classification returning one bit, which is the shape where
the small model wins.

## Answer integrity

- **No evidence, no answer.** A question that retrieves nothing returns zero citations, a grade of
  `incorrect`, and an explicit gap — never a plausible-sounding guess.
- **Every `[n]` marker is validated** against the citations actually emitted. An unresolved marker is
  stripped, the grade downgraded, and the discrepancy disclosed.
- **Trace durations are measured**, not estimated.
- **Every cited claim is checked against the passage it cites.** A claim the evidence does not state
  downgrades the grade and is quoted back as a gap. Measured: recall 0.909 on unsupported claims,
  1.000 on leaving correct ones alone — see `evals/baselines/entailment_2026-09-04.md`. It caught a
  real misattribution on its first live answer.
- **The limit, stated plainly:** a claim is the span its marker terminates, which for a
  paragraph-trailing marker is the whole paragraph, and quantifier scope ("all" widened from "exact")
  is the class it still misses.

Ingestion is deliberately **read-only**. Groundwork never writes back to GitHub, Jira or Slack, and
takes no actions on your behalf. Comparable products sync bidirectionally; this is a chosen boundary,
not a missing feature.

## Architecture

```
Next.js UI (Vercel)
  ↓  server-side route handler attaches the API key
FastAPI backend (EC2, behind Caddy + Let's Encrypt)
  ↓
deterministic intent router — ordered by specificity, no LLM
  ├── GitHub / Jira / Slack connectors        incremental, overlap-cursor polling
  ├── typed SQL for exact questions           structured_github.py, structured_jira.py
  └── hybrid retrieval                        Postgres full-text + pgvector, fused by RRF
  ↓
CRAG-style grading → bounded corrective loop → citation validation
  ↓
a cited answer, or an explicit unresolved gap
```

The agent is a LangGraph `StateGraph`: 14 nodes, 17 edges, with one real cycle
(`grade → correct → grade`). Routing inside it is deterministic by design — the branches a model
would choose between all converge on the same retrieval path, so a planner would add seconds of
latency and non-reproducibility for no behavioural difference.

**Chat is pluggable; embeddings are not.** A `ChatClient` protocol has two implementations (Ollama
and any OpenAI-compatible endpoint, verified against Groq). Embeddings stay local because the schema
hardcodes `Vector(dim=768)` from `embeddinggemma` — a different embedding model is a different vector
space, and retrieval would silently return unrelated chunks rather than fail.

## Local setup

```bash
cp .env.example .env
docker compose up -d postgres
```

**Run Ollama on the host, not in Docker.** This is not a preference: Docker Desktop cannot reach the
Apple Silicon GPU, so a containerised Ollama runs every token on emulated CPU — measured on an M4,
7.2 tok/s in the container against 40 tok/s on the host, the difference between a ~40 s answer and a
~4 s one.

```bash
brew install ollama          # or https://ollama.com/download
ollama serve
ollama pull llama3.2:3b      # grading and synthesis
ollama pull embeddinggemma   # embeddings
```

`scripts/check_local.sh` reports which Ollama is answering and measures throughput, so a silent
regression back to CPU is visible rather than merely slow. The compose file keeps an `ollama` service
behind a profile for hosts where the container *can* reach a GPU:

```bash
docker compose --profile bundled-ollama up -d   # then OLLAMA_BASE_URL=http://ollama:11434
```

Build and migrate:

```bash
docker compose build backend
docker compose run --rm --no-deps backend alembic upgrade head
curl http://localhost:8000/health/ollama    # reports chat and embedding providers separately
```

### Onboard a project

`project_id` is required on every query. It used to default to a demo project, which meant a caller
who forgot it got a confident answer about a project they had not asked about.

```bash
curl -X POST http://localhost:8000/projects \
  -H 'Content-Type: application/json' -H 'X-API-Key: change-me' \
  -d '{"id":"project-x","name":"Project X","repo":"owner/repository"}'

curl -X POST 'http://localhost:8000/projects/project-x/sync/github?max_commits=500' \
  -H 'X-API-Key: change-me'

curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' -H 'X-API-Key: change-me' \
  -d '{"project_id":"project-x","query":"What was the last commit by <author>?"}'
```

Jira and Slack attach the same way, after setting their variables from `.env.example`:

```bash
curl -X PUT http://localhost:8000/projects/project-x/connectors/jira \
  -H 'Content-Type: application/json' -H 'X-API-Key: change-me' \
  -d '{"project_key":"ASK"}'

curl -X PUT http://localhost:8000/projects/project-x/connectors/slack \
  -H 'Content-Type: application/json' -H 'X-API-Key: change-me' \
  -d '{"channel_ids":["C0…"]}'
```

Slack is indexed **thread-per-document**: a root message and its replies become one piece of
evidence. It is the only source that records *why* a decision was made — GitHub and Jira record what
changed and what is left.

Add `?background=true` to any sync to have it run off the request thread and poll the matching `GET`
endpoint instead. Synchronous is the default because the eval harness syncs then queries
immediately.

## Evaluation

Three tiers, and only two of them gate.

```bash
cd backend
.venv/bin/python -m evals.runner --dataset evals/askbase.jsonl --sync-before --fail-under 1.0
.venv/bin/python -m evals.runner --dataset evals/jira_askbase.jsonl --sync-before --fail-under 1.0
.venv/bin/python -m evals.generalization_runner --project-id <any-project>
.venv/bin/python -m evals.conversation_runner --trials 3 --sync-before --fail-under 1.0
.venv/bin/python -m evals.conversation_runner --fast     # ~90s instead of ~50min
```

The conversation suite reports two things separately, because only one is decided by code. **Hard
checks** — which route ran, the grade, citation presence, whether every `[n]` resolves — gate at
1.000. **Measured checks** — whether a follow-up resolved and to what — are run over `--trials`
passes and reported as a rate, because asserting a 3B model's output once turns variance into a red
build. Conversations marked `known_limitation` report in their own bucket and never gate; deleting
the marker is how a fix is recorded.

Retrieval and grading are measured directly, without the HTTP layer:

```bash
docker compose run --rm --no-deps backend python -m evals.retrieval_runner
docker compose run --rm --no-deps backend python -m evals.grading_runner
```

### Tests

Two tiers. The default needs nothing but the virtualenv, stays under a second, and opens no socket —
the LLM, the database and the rate limiter are all forced offline, because a tier whose result
depends on what the developer happens to have running is not a tier.

```bash
cd backend
.venv/bin/python -m pytest                       # 356 tests
```

The integration tier runs the real SQL — the fusion query, content-hash upserts, the sync state
machine, citation snapshots — against a database built by the shipped migrations. It also runs the
exact-answer evaluation dataset against a seeded corpus, through the same checker the live runner
uses: those cases route to typed SQL and make no model call, so they gate in CI. Skipped unless
`TEST_DATABASE_URL` is set; embeddings are stubbed, so it needs no credentials and no Ollama:

```bash
docker compose up -d postgres
TEST_DATABASE_URL="postgresql+asyncpg://groundwork:groundwork@127.0.0.1:5433/groundwork_test" \
  .venv/bin/python -m pytest -m integration      # 131 tests
```

Port **5433** is deliberate: the container also publishes 5432, but a developer machine running its
own PostgreSQL wins that port and silently shadows the container.

## Deployment

Frontend on Vercel, backend on EC2. See `docs/DEPLOYMENT.md`.

```
browser → Vercel (free TLS)  →  Caddy + Let's Encrypt  →  backend
                                                            ├── postgres      Docker network only
                                                            ├── ollama        embeddings only
                                                            └── Groq          chat
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Only Caddy is published. The chat model is deliberately **not installed** on the instance — a RAG
turn measured 67.9 s there against 8.1 s on Metal, so generation goes to a hosted provider while
embeddings stay local at 75–88 ms per query.

**Operations:** JSON logs on stdout with a correlation id echoed as `X-Request-Id`, and Prometheus
metrics at `/metrics`. Rate limiting has two ceilings — per client, and a global one, because the
hosted free tier is metered per *organization* and enough individually-polite callers would drain it
between them.

## API

    GET  /health                                   public
    GET  /health/ollama                            chat and embedding providers, reported separately
    GET  /health/database
    GET  /metrics                                  Prometheus exposition
    POST /query                                    optional conversation_id
    POST /projects                                 GET /projects
    GET  /projects/{id}/timeline
    PUT  /projects/{id}/connectors/{jira,slack}
    POST /projects/{id}/sync/{github,jira,slack}   ?background=true
    GET  /projects/{id}/sync/{github,jira,slack}
    GET  /conversations/{id}                       GET /conversations/{id}/trace

All non-health endpoints require `X-API-Key`.

`POST /query` accepts an optional `conversation_id`. A follow-up that depends on earlier turns —
*"who is it assigned to?"* — is rewritten into a standalone question **before** routing, so it reaches
the same deterministic SQL path the question it follows did; the standalone form comes back as
`resolved_query`. A self-contained question skips resolution entirely, so exact-answer queries stay
free of model latency. An unknown or cross-project `conversation_id` is a 404.

Connector syncs follow provider pagination, capture rate-limit headers, and use a 10-minute overlap
cursor after the first successful run. Documents are atomically upserted by content hash, so
unchanged content keeps its chunks and its embeddings.

## Known limitations

Kept deliberately, with reasons, rather than quietly omitted:

- **Polling, not webhooks.** No public ingress and no secret rotation to manage. The cost is real: a
  freshly pushed commit with an unusually old author timestamp can fall outside the overlap window.
- **Entailment is judged per claim span, not per sentence** — see Answer integrity above.
- **One unanswerable question is accepted** by the grader, because the corpus grew Slack timing
  metrics that superficially resemble the figure asked for.
- **Author identity is untested against a repository with many distinct contributors.**
- **Multi-intent questions are not supported**, and are declined rather than planned: citation
  ordinals would have to be renumbered across two evidence sets, which is one of the two load-bearing
  invariants.
