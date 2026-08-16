import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from app.agent.graph import run_agent
from app.connectors.github import GitHubCommit
from app.db.models import ConnectorSyncState
from app.db.session import SessionFactory
from app.models.schemas import QueryRequest
from app.services.ingestion import (
    github_commit_documents,
    ingest_documents,
    seed_synthetic_workspace,
)


async def main() -> None:
    commits = [
        GitHubCommit(
            sha="1111111oldcommit",
            message="Older commit",
            author="Raghav Sharma",
            author_email="raghav@example.com",
            author_login="raghav-dev",
            committer="Raghav Sharma",
            authored_at="2026-08-09T09:00:00Z",
            committed_at="2026-08-09T09:05:00Z",
            url="https://github.com/acme/project/commit/1111111oldcommit",
        ),
        GitHubCommit(
            sha="2222222newcommit",
            message="Newest commit by Raghav",
            author="Raghav Sharma",
            author_email="raghav@example.com",
            author_login="raghav-dev",
            committer="Raghav Sharma",
            authored_at="2026-08-10T09:00:00Z",
            committed_at="2026-08-10T09:05:00Z",
            url="https://github.com/acme/project/commit/2222222newcommit",
        ),
    ]
    async with SessionFactory() as session:
        await seed_synthetic_workspace(session)
        await ingest_documents(
            session,
            github_commit_documents("project-atlas", commits),
        )
        statement = insert(ConnectorSyncState).values(
            project_id="project-atlas",
            source_type="github",
            status="succeeded",
            last_started_at=datetime.now(UTC),
            last_succeeded_at=datetime.now(UTC),
        )
        await session.execute(
            statement.on_conflict_do_update(
                constraint="uq_connector_sync_states_project_id",
                set_={
                    "status": "succeeded",
                    "last_succeeded_at": datetime.now(UTC),
                },
            )
        )
        await session.commit()

    async with SessionFactory() as session:
        response = await run_agent(
            QueryRequest(
                query="What was the last commit by Raghav on Project Atlas?",
                project_id="project-atlas",
            ),
            session,
        )

    assert "2222222" in response.answer
    assert len(response.citations) == 1
    assert response.tools_used == ["planner", "structured_github_query"]
    print(
        json.dumps(
            {
                "answer": response.answer,
                "retrieval_grade": response.retrieval_grade,
                "citation": response.citations[0].model_dump(),
                "tools_used": response.tools_used,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
