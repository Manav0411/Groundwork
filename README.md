# Engineering Project Intelligence Agent

A self-hosted Agent-as-a-Service product for engineering project intelligence.

The system answers questions like:

- "Generate a weekly brief for Project Atlas."
- "What blockers are delaying Project Atlas?"
- "What was the last commit by Raghav on Project Atlas?"
- "What decisions were made about the database migration?"

The project is free-first: Ollama/local open models are the default, PostgreSQL + pgvector is self-hosted, and paid LLM APIs are not required.

## Current implementation

- `backend/` — FastAPI + LangGraph-ready backend
- `frontend/` — Next.js demo UI
- `docker-compose.yml` — Postgres, Ollama, backend, frontend
- `docs/` — scope, architecture, tradeoffs, build journey, evaluation, standardization, deployment

The backend now persists projects, source documents, chunks, conversations, query runs,
citations, retrieved evidence, and agent traces. Retrieval combines PostgreSQL full-text search
with pgvector cosine similarity and falls back to full-text search when embeddings are offline.

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

Backend:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,eval]"
uvicorn app.main:app --reload
```

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
- `GET /projects`
- `GET /projects/{project_id}/timeline`
- `GET /conversations/{conversation_id}/trace`

`POST /sync/github` accepts `project_id`, `repo`, and `limit` query parameters. Synced commits
are atomically upserted, chunked, embedded when Ollama is available, and immediately searchable.

All non-health endpoints require:

```http
X-API-Key: change-me
```

## Documentation

Start with:

- [Project scope](docs/PROJECT_SCOPE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Tradeoffs](docs/TRADEOFFS.md)
- [Build journey](docs/BUILD_JOURNEY.md)
- [Evaluation](docs/EVALUATION.md)
- [Standardization](docs/STANDARDIZATION.md)
- [Deployment](docs/DEPLOYMENT.md)
