"""Initial persistence and hybrid retrieval schema.

Revision ID: 20260809_0001
Revises:
Create Date: 2026-08-09
"""
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260809_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create extension if not exists vector")
    op.create_table(
        "projects",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("repo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("health", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "health in ('green', 'yellow', 'red', 'gray')",
            name=op.f("ck_projects_valid_health"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_table(
        "source_documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_source_documents_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_source_documents"),
        sa.UniqueConstraint("project_id", "source_type", "external_id", name="uq_source_documents_project_id"),
    )
    op.create_index(
        "ix_source_documents_project_source_time",
        "source_documents",
        ["project_id", "source_type", "source_created_at"],
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=768), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', coalesce(content, ''))", persisted=True),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.id"], name="fk_document_chunks_document_id_source_documents", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_document_chunks_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_id"),
    )
    op.create_index("ix_document_chunks_project_document", "document_chunks", ["project_id", "document_id"])
    op.create_index("ix_document_chunks_search_vector", "document_chunks", ["search_vector"], postgresql_using="gin")
    op.execute(
        "create index ix_document_chunks_embedding_hnsw on document_chunks "
        "using hnsw (embedding vector_cosine_ops) where embedding is not null"
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_conversations_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.UniqueConstraint("public_id", name="uq_conversations_public_id"),
    )
    op.create_index("ix_conversations_project_created", "conversations", ["project_id", "created_at"])
    op.create_table(
        "query_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_type", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("retrieval_grade", sa.Text(), nullable=False),
        sa.Column("tools_used", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("unresolved_gaps", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "retrieval_grade in ('correct', 'ambiguous', 'incorrect')",
            name=op.f("ck_query_runs_valid_retrieval_grade"),
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name="fk_query_runs_conversation_id_conversations", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_query_runs"),
    )
    op.create_index("ix_query_runs_conversation_created", "query_runs", ["conversation_id", "created_at"])
    op.create_table(
        "query_citations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("query_run_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["source_documents.id"], name="fk_query_citations_document_id_source_documents", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], name="fk_query_citations_query_run_id_query_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_query_citations"),
        sa.UniqueConstraint("query_run_id", "ordinal", name="uq_query_citations_query_run_id"),
    )
    op.create_index("ix_query_citations_document_id", "query_citations", ["document_id"])
    op.create_table(
        "retrieved_evidence",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("query_run_id", sa.BigInteger(), nullable=False),
        sa.Column("chunk_id", sa.BigInteger(), nullable=True),
        sa.Column("citation_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.Column("authority", sa.Float(), nullable=False),
        sa.Column("lexical_score", sa.Float(), nullable=False),
        sa.Column("vector_score", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], name="fk_retrieved_evidence_chunk_id_document_chunks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], name="fk_retrieved_evidence_query_run_id_query_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_retrieved_evidence"),
    )
    op.create_index("ix_retrieved_evidence_chunk_id", "retrieved_evidence", ["chunk_id"])
    op.create_index("ix_retrieved_evidence_query_run_id", "retrieved_evidence", ["query_run_id"])
    op.create_table(
        "trace_steps",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("query_run_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'completed', 'failed')",
            name=op.f("ck_trace_steps_valid_status"),
        ),
        sa.ForeignKeyConstraint(["query_run_id"], ["query_runs.id"], name="fk_trace_steps_query_run_id_query_runs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_trace_steps"),
        sa.UniqueConstraint("query_run_id", "sequence", name="uq_trace_steps_query_run_id"),
    )


def downgrade() -> None:
    op.drop_table("trace_steps")
    op.drop_index("ix_retrieved_evidence_query_run_id", table_name="retrieved_evidence")
    op.drop_index("ix_retrieved_evidence_chunk_id", table_name="retrieved_evidence")
    op.drop_table("retrieved_evidence")
    op.drop_index("ix_query_citations_document_id", table_name="query_citations")
    op.drop_table("query_citations")
    op.drop_index("ix_query_runs_conversation_created", table_name="query_runs")
    op.drop_table("query_runs")
    op.drop_index("ix_conversations_project_created", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_document_chunks_embedding_hnsw", table_name="document_chunks", postgresql_using="hnsw")
    op.drop_index("ix_document_chunks_search_vector", table_name="document_chunks", postgresql_using="gin")
    op.drop_index("ix_document_chunks_project_document", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_source_documents_project_source_time", table_name="source_documents")
    op.drop_table("source_documents")
    op.drop_table("projects")
