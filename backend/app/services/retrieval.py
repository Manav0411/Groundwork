from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Float, cast, desc, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DocumentChunk, SourceDocument
from app.models.schemas import Citation, EvidenceItem
from app.services.llm import OllamaClient

# Reciprocal rank fusion constant. The RRF paper's default of 60 is large relative to a candidate
# depth of 30, which compresses the within-list rank spread so much that a weak match appearing in
# *both* lists outranks a strong match appearing in one. Measured on evals/retrieval_dataset.jsonl,
# that cost 0.115 MRR. 10 keeps rank position meaningful.
RRF_K = 10

# How deep each retriever goes before fusion. Bounding the vector side is the point: it previously
# admitted every embedded chunk in the project, which made the query almost irrelevant to what came
# back.
CANDIDATE_DEPTH = 30

# Fusion weights chosen by sweeping rrf_k x candidate_depth x weights against
# evals/retrieval_dataset.jsonl. On this corpus the vector retriever is decisively the stronger
# signal (MRR 1.000 on its own), and every configuration that let lexical rank drive the ordering
# lost recall and MRR. Lexical therefore acts as a tie-breaker rather than a driver.
#
# This weighting is tuned on 16 cases over 44 documents and should be re-swept as the corpus grows:
# exact identifiers (SHAs, ticket keys, error strings) are where lexical retrieval earns its weight,
# and short commit messages are close to the worst case for ts_rank_cd.
LEXICAL_WEIGHT = 0.15
VECTOR_WEIGHT = 1.0


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


def _or_tsquery(query: str):
    """Build a tsquery whose terms are OR-ed rather than AND-ed.

    `websearch_to_tsquery` joins every term with `&`, so a natural-language question demanded that
    one chunk contain all of its stemmed terms — measured against the live index, `ts_rank_cd`
    returned 0 even for questions whose words were plainly in the corpus. Stemming the query with
    `to_tsvector` and re-joining the lexemes with `|` lets partial matches through, and lets
    `ts_rank_cd` rank by how many terms matched.

    `nullif` guards the all-stopword case: `to_tsquery('')` raises a syntax error, whereas
    `to_tsquery(NULL)` yields NULL and the `@@` match is simply never true.
    """
    lexemes = func.array_to_string(
        func.tsvector_to_array(func.to_tsvector("english", query)), " | "
    )
    return func.to_tsquery("english", func.nullif(lexemes, ""))


async def hybrid_retrieve(
    session: AsyncSession,
    project_id: str,
    query: str,
    limit: int = 8,
    ollama: OllamaClient | None = None,
    *,
    rrf_k: int = RRF_K,
    candidate_depth: int = CANDIDATE_DEPTH,
    lexical_weight: float = LEXICAL_WEIGHT,
    vector_weight: float = VECTOR_WEIGHT,
) -> list[RetrievedRecord]:
    query_embedding: list[float] | None = None
    if ollama is not None:
        try:
            query_embedding = (await ollama.embed([query]))[0]
        except Exception:
            pass

    ts_query = _or_tsquery(query)
    lexical_score = func.ts_rank_cd(DocumentChunk.search_vector, ts_query)

    # Candidate set one: lexical matches only, ranked by text relevance.
    lexical_cte = (
        select(
            DocumentChunk.id.label("chunk_id"),
            lexical_score.label("score"),
            func.row_number().over(order_by=[desc(lexical_score), DocumentChunk.id]).label("rank"),
        )
        .where(
            DocumentChunk.project_id == project_id,
            DocumentChunk.search_vector.op("@@")(ts_query),
        )
        .limit(candidate_depth)
        .cte("lexical_candidates")
    )

    if query_embedding is not None:
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        vector_cte = (
            select(
                DocumentChunk.id.label("chunk_id"),
                (1 - distance).label("score"),
                func.row_number().over(order_by=[distance, DocumentChunk.id]).label("rank"),
            )
            .where(
                DocumentChunk.project_id == project_id,
                DocumentChunk.embedding.is_not(None),
            )
            .limit(candidate_depth)
            .cte("vector_candidates")
        )
    else:
        # Embeddings unavailable: retrieval degrades to lexical-only, which is a documented
        # contract. An empty CTE keeps the fusion query shape identical.
        vector_cte = (
            select(
                DocumentChunk.id.label("chunk_id"),
                literal(0.0).label("score"),
                literal(0).label("rank"),
            )
            .where(literal(False))
            .cte("vector_candidates")
        )

    lexical_rrf = func.coalesce(1.0 / (rrf_k + cast(lexical_cte.c.rank, Float)), 0.0)
    vector_rrf = func.coalesce(1.0 / (rrf_k + cast(vector_cte.c.rank, Float)), 0.0)
    fused = (lexical_weight * lexical_rrf + vector_weight * vector_rrf).label("rrf_score")

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
            func.coalesce(lexical_cte.c.score, 0.0).label("lexical_score"),
            func.coalesce(vector_cte.c.score, 0.0).label("vector_score"),
            fused,
        )
        .select_from(lexical_cte)
        .join(
            vector_cte,
            vector_cte.c.chunk_id == lexical_cte.c.chunk_id,
            full=True,
        )
        .join(
            DocumentChunk,
            DocumentChunk.id == func.coalesce(lexical_cte.c.chunk_id, vector_cte.c.chunk_id),
        )
        .join(SourceDocument, SourceDocument.id == DocumentChunk.document_id)
        .order_by(desc(fused), SourceDocument.source_created_at.desc().nullslast())
        # Over-fetch so that collapsing multiple chunks of one document still fills `limit`.
        .limit(limit * 3)
    )
    rows = (await session.execute(statement)).mappings().all()

    records: list[RetrievedRecord] = []
    seen_documents: set[int] = set()
    for row in rows:
        # One document contributes its best-ranked chunk only, so a long document cannot crowd the
        # result set out from under other sources.
        if row["document_id"] in seen_documents:
            continue
        seen_documents.add(row["document_id"])
        records.append(
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
        )
        if len(records) == limit:
            break
    return records


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
