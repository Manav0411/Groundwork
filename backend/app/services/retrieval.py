from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentChunk, SourceDocument
from app.models.schemas import Citation, EvidenceItem
from app.services.llm import OllamaClient


@dataclass(frozen=True)
class RetrievedRecord:
    chunk_id: int
    document_id: int
    source_type: str
    title: str
    content: str
    url: str | None
    source_timestamp: datetime | None
    authority: float
    lexical_score: float
    vector_score: float


async def hybrid_retrieve(
    session: AsyncSession,
    project_id: str,
    query: str,
    limit: int = 8,
    ollama: OllamaClient | None = None,
) -> list[RetrievedRecord]:
    query_embedding: list[float] | None = None
    if ollama is not None:
        try:
            query_embedding = (await ollama.embed([query]))[0]
        except Exception:
            pass

    ts_query = func.websearch_to_tsquery("english", query)
    lexical = func.ts_rank_cd(DocumentChunk.search_vector, ts_query)
    vector = literal(0.0)
    filters = [
        DocumentChunk.project_id == project_id,
        DocumentChunk.search_vector.op("@@")(ts_query),
    ]
    if query_embedding is not None:
        vector = func.coalesce(1 - DocumentChunk.embedding.cosine_distance(query_embedding), 0.0)
        filters[1] = or_(filters[1], DocumentChunk.embedding.is_not(None))

    score = lexical * 0.45 + vector * 0.55
    statement = (
        select(
            DocumentChunk.id.label("chunk_id"),
            SourceDocument.id.label("document_id"),
            SourceDocument.source_type,
            SourceDocument.title,
            DocumentChunk.content,
            SourceDocument.url,
            SourceDocument.source_created_at,
            SourceDocument.source_metadata,
            lexical.label("lexical_score"),
            vector.label("vector_score"),
        )
        .join(SourceDocument, SourceDocument.id == DocumentChunk.document_id)
        .where(*filters)
        .order_by(desc(score), SourceDocument.source_created_at.desc().nullslast())
        .limit(limit)
    )
    rows = (await session.execute(statement)).mappings().all()
    return [
        RetrievedRecord(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            source_type=row["source_type"],
            title=row["title"],
            content=row["content"],
            url=row["url"],
            source_timestamp=row["source_created_at"],
            authority=float(row["source_metadata"].get("authority", 0.8)),
            lexical_score=float(row["lexical_score"] or 0),
            vector_score=float(row["vector_score"] or 0),
        )
        for row in rows
    ]


def records_to_response(
    records: list[RetrievedRecord],
) -> tuple[list[EvidenceItem], list[Citation]]:
    evidence: list[EvidenceItem] = []
    citations: list[Citation] = []
    for ordinal, item in enumerate(records, start=1):
        timestamp = item.source_timestamp.isoformat() if item.source_timestamp else None
        citations.append(
            Citation(
                id=ordinal,
                source_type=item.source_type,
                title=item.title,
                url=item.url,
                timestamp=timestamp,
            )
        )
        evidence.append(
            EvidenceItem(
                id=f"chunk-{item.chunk_id}",
                source_type=item.source_type,
                title=item.title,
                snippet=item.content[:500],
                citation_id=ordinal,
                authority=item.authority,
            )
        )
    return evidence, citations
