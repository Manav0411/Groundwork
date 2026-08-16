import asyncio
import json

from sqlalchemy import func, select

from app.db.models import DocumentChunk
from app.db.session import SessionFactory
from app.services.ingestion import seed_synthetic_workspace
from app.services.llm import OllamaClient
from app.services.retrieval import hybrid_retrieve


async def main() -> None:
    client = OllamaClient()
    probe = await client.embed(["engineering project blocker"])
    if len(probe[0]) != 768:
        raise RuntimeError(f"Unexpected embedding dimension: {len(probe[0])}")

    async with SessionFactory() as session:
        ingestion = await seed_synthetic_workspace(session)

    async with SessionFactory() as session:
        embedded_chunks = await session.scalar(
            select(func.count(DocumentChunk.id)).where(DocumentChunk.embedding.is_not(None))
        )
        records = await hybrid_retrieve(
            session,
            project_id="project-atlas",
            query="payment gateway blocker",
            ollama=client,
        )

    if not records or not any(record.vector_score > 0 for record in records):
        raise RuntimeError("Live vector retrieval returned no vector-scored evidence.")
    print(
        json.dumps(
            {
                "embedding_dimension": len(probe[0]),
                "ingestion": ingestion,
                "embedded_chunks": embedded_chunks,
                "top_result": {
                    "title": records[0].title,
                    "lexical_score": records[0].lexical_score,
                    "vector_score": records[0].vector_score,
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
