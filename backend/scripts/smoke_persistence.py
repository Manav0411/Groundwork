import asyncio
import json

from sqlalchemy import func, select

from app.agent.graph import run_agent
from app.db.models import Conversation, DocumentChunk, QueryRun, SourceDocument
from app.db.session import SessionFactory
from app.models.schemas import QueryRequest
from app.services.ingestion import seed_synthetic_workspace
from app.services.persistence import load_trace


async def main() -> None:
    async with SessionFactory() as session:
        ingestion = await seed_synthetic_workspace(session)

    async with SessionFactory() as session:
        response = await run_agent(
            QueryRequest(query="What payment gateway blockers are delaying Project Atlas?"),
            session,
        )

    async with SessionFactory() as session:
        trace = await load_trace(session, response.conversation_id)
        counts = {
            "documents": await session.scalar(select(func.count(SourceDocument.id))),
            "chunks": await session.scalar(select(func.count(DocumentChunk.id))),
            "conversations": await session.scalar(select(func.count(Conversation.id))),
            "query_runs": await session.scalar(select(func.count(QueryRun.id))),
        }

    print(
        json.dumps(
            {
                "ingestion": ingestion,
                "conversation_id": response.conversation_id,
                "evidence_count": len(response.evidence),
                "persisted_trace_steps": len(trace or []),
                "counts": counts,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
