# Engineering Project Intelligence Agent

A self-hosted Agent-as-a-Service product for engineering project intelligence.

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
citation validation, and evaluation gates are complete. Query routing is a deterministic intent
router rather than a planner-driven graph. Retrieval grading is derived from what retrieval
returned; there is no corrective retrieval loop yet. The hybrid retriever's lexical leg
under-contributes because its candidate filter is too permissive, so summary-style questions are
weaker than the exact-answer paths.

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
docker exec -it agentic-rag-ollama ollama pull qwen3:8b
docker exec -it agentic-rag-ollama ollama pull embeddinggemma
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

Run the live Jira gate with the same runner:

```bash
.venv/bin/python -m evals.runner \
  --dataset evals/jira_askbase.jsonl \
  --sync-before \
  --fail-under 1.0
```

Add `--semantic --judge-model qwen3:8b` only when the local Ollama judge model and the
`eval` dependency extra are installed. Exact GitHub checks do not require an LLM.

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
