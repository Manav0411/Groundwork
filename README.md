# Groundwork

Engineering knowledge, with evidence. A self-hosted agent that answers questions about your engineering projects from GitHub and Jira, and cites every claim.

The system answers questions like:

- "Generate a weekly brief for Project Atlas."
- "What blockers are delaying Project Atlas?"
- "What was the last commit by Raghav on Project Atlas?"
- "What is the status of ASK-6?"
- "What blockers are open in AskBase?"
- "What decisions were made about the database migration?"

The project is free-first: Ollama/local open models are the default, PostgreSQL + pgvector is self-hosted, and paid LLM APIs are not required.

## Current implementation

- `backend/` — FastAPI backend with a deterministic intent router
- `frontend/` — Next.js demo UI
- `docker-compose.yml` — Postgres, Ollama, backend, frontend

The backend persists projects, source documents, chunks, conversations, query runs, citations,
retrieved evidence, and agent traces. Retrieval combines PostgreSQL full-text search with pgvector
cosine similarity and falls back to full-text search when embeddings are offline. Exact questions
("latest commit by X", "status of ASK-6") bypass semantic retrieval entirely and are answered by
typed SQL, so embedding similarity never decides factual ordering.

**Current limitations, stated plainly:** the connectors, persistence, exact-answer SQL paths,
citation validation, retrieval grading, and evaluation gates are complete. The agent is a
LangGraph `StateGraph` whose corrective loop is a real bounded cycle; routing within it is
deterministic by design rather than model-driven, because the branches a model would choose between
all converge on the same retrieval path. Retrieval and grading quality are
measured against a labelled set (`backend/evals/baselines/`): retrieval reaches MRR 1.000 but its
lexical half contributes little on short commit messages, and the grader reaches 0.875 sufficiency
accuracy, refusing all unanswerable questions but needing the corrective loop to recover questions
phrased in vocabulary the corpus does not use. Grading costs roughly 8 s per call on CPU.

Ingestion is deliberately **read-only**. Groundwork never writes back to GitHub or Jira and its
agent takes no actions on your behalf — comparable products sync bi-directionally and let agents
modify work items, so this is a chosen boundary rather than a missing feature. The system is
designed to answer questions with traceable evidence, and to refuse when it has none.

### Answer integrity

- No evidence, no answer. A question that retrieves nothing returns zero citations, a grade of
  `incorrect`, and an explicit unresolved gap — never a plausible-sounding guess.
- Every `[n]` citation marker is checked against the citations actually emitted. An unresolved
  marker is stripped, the grade downgraded, and the discrepancy disclosed.
- Agent trace durations are measured, not estimated.

## Local setup

Copy environment variables:

```bash
cp .env.example .env
```

Start infrastructure:

```bash
docker compose up -d postgres ollama
```

Build the backend and apply migrations:

```bash
docker compose build backend
docker compose run --rm --no-deps backend alembic upgrade head
```

Pull local models:

```bash
docker exec -it groundwork-ollama ollama pull qwen3:8b
docker exec -it groundwork-ollama ollama pull embeddinggemma
```

Verify the configured Ollama model:

```bash
curl http://localhost:8000/health/ollama
curl http://localhost:8000/health/database
```

Load the development workspace, then ask a persisted query:

```bash
curl -X POST http://localhost:8000/ingest/workspace \
  -H 'X-API-Key: change-me'

curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-me' \
  -d '{"query":"What payment gateway blockers are delaying Project Atlas?"}'
```

Onboard and sync a real GitHub repository:

```bash
curl -X POST http://localhost:8000/projects \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-me' \
  -d '{"id":"project-x","name":"Project X","repo":"owner/repository"}'

curl -X POST 'http://localhost:8000/projects/project-x/sync/github?max_commits=500' \
  -H 'X-API-Key: change-me'

curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-me' \
  -d '{"project_id":"project-x","query":"What was the last commit by Raghav on Project X?"}'
```

Connect and sync a Jira Cloud project after setting `JIRA_SITE_URL`, `JIRA_API_TOKEN`, and the
other Jira variables from `.env.example`:

```bash
curl -X PUT http://localhost:8000/projects/project-x/connectors/jira \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-me' \
  -d '{"project_key":"ASK"}'

curl -X POST 'http://localhost:8000/projects/project-x/sync/jira?max_issues=500' \
  -H 'X-API-Key: change-me'

curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-me' \
  -d '{"project_id":"project-x","query":"What is the status of ASK-6?"}'
```

Backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,eval]"
uvicorn app.main:app --reload
```

Run the deterministic AskBase release gate against a running backend:

```bash
cd backend
.venv/bin/python -m evals.runner \
  --dataset evals/askbase.jsonl \
  --sync-before \
  --fail-under 1.0
```

Measure retrieval and grading quality against the labelled set (run inside the container — a host
PostgreSQL often owns `localhost:5432` and shadows the published port):

```bash
docker compose run --rm --no-deps backend python -m evals.retrieval_runner
docker compose run --rm --no-deps backend python -m evals.grading_runner
```

Grading needs a local grader model: `docker exec -it groundwork-ollama ollama pull llama3.2:3b`.

Run the live Jira gate with the same runner:

```bash
.venv/bin/python -m evals.runner \
  --dataset evals/jira_askbase.jsonl \
  --sync-before \
  --fail-under 1.0
```

Add `--semantic --judge-model qwen3:8b` only when the local Ollama judge model and the
`eval` dependency extra are installed. Exact GitHub checks do not require an LLM.

### Tests

Two tiers. The default run needs nothing but the virtualenv, stays under a second, and never opens
a socket:

```bash
cd backend
.venv/bin/python -m pytest
```

The integration tier runs the real SQL — the fusion query, the content-hash upsert, the connector
sync state machine, the citation snapshot — against a database built by the shipped migrations. It
is skipped unless `TEST_DATABASE_URL` is set, and embeddings are stubbed, so it needs no
credentials and no Ollama:

```bash
docker compose up -d postgres
cd backend
TEST_DATABASE_URL="postgresql+asyncpg://groundwork:groundwork@127.0.0.1:5433/groundwork_test" \
  .venv/bin/python -m pytest -m integration
```

Port **5433** is deliberate. The container also publishes 5432, but a developer machine running its
own PostgreSQL wins that port and silently shadows the container; 5433 always reaches the
container. The tests create and truncate their own `groundwork_test` database and never touch the
`groundwork` database holding an indexed corpus.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## API

- `GET /health`
- `GET /health/ollama`
- `GET /health/database`
- `POST /query`
- `POST /ingest/workspace`
- `POST /sync/github`
- `POST /projects`
- `POST /projects/{project_id}/sync/github`
- `GET /projects/{project_id}/sync/github`
- `PUT /projects/{project_id}/connectors/jira`
- `POST /projects/{project_id}/sync/jira`
- `GET /projects/{project_id}/sync/jira`
- `GET /projects`
- `GET /projects/{project_id}/timeline`
- `GET /conversations/{conversation_id}/trace`

GitHub sync follows API pagination, stores rate-limit status, and uses an overlap cursor after the
first successful run. Synced commits are atomically upserted, chunked, embedded when Ollama is
available, and immediately searchable.

Jira sync uses Atlassian's Cloud ID routing and enhanced JQL pagination. Scoped tokens are sent as
Bearer credentials, issues are incrementally refreshed with an overlap cursor, and exact issue,
assignee, and blocker questions use deterministic SQL rather than semantic guessing.

All non-health endpoints require:

```http
X-API-Key: change-me
```
