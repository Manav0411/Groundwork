import asyncio
import json

from sqlalchemy import desc, select

from app.agent.graph import run_agent
from app.db.models import SourceDocument
from app.db.session import SessionFactory
from app.models.schemas import ProjectSummary, QueryRequest
from app.services.github_sync import sync_github_project
from app.services.ingestion import upsert_project


async def main() -> None:
    async with SessionFactory() as session:
        await upsert_project(
            session,
            ProjectSummary(
                id="github-real-smoke",
                name="GitHub Real Smoke",
                repo="octocat/Hello-World",
                status="Test",
                health="gray",
            ),
        )
        await session.commit()
        report = await sync_github_project(session, "github-real-smoke", max_commits=50)

    async with SessionFactory() as session:
        latest = await session.scalar(
            select(SourceDocument)
            .where(
                SourceDocument.project_id == "github-real-smoke",
                SourceDocument.source_type == "github",
                SourceDocument.author.is_not(None),
            )
            .order_by(desc(SourceDocument.source_created_at), desc(SourceDocument.id))
            .limit(1)
        )
        if latest is None or latest.author is None:
            raise RuntimeError("Real GitHub sync returned no authored commits.")
        response = await run_agent(
            QueryRequest(
                query=f"What was the last commit by {latest.author} on Project GitHub Real Smoke?",
                project_id="github-real-smoke",
            ),
            session,
        )

    if not response.citations:
        raise RuntimeError("Structured commit query returned no citation.")
    print(
        json.dumps(
            {
                "sync": {
                    "repo": report.repo,
                    "fetched": report.fetched,
                    "pages_fetched": report.pages_fetched,
                    "rate_limit_remaining": report.rate_limit_remaining,
                },
                "queried_author": latest.author,
                "answer": response.answer,
                "citation_url": response.citations[0].url,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
