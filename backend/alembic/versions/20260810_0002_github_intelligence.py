"""Add GitHub author identities and connector sync state.

Revision ID: 20260810_0002
Revises: 20260809_0001
Create Date: 2026-08-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0002"
down_revision: str | None = "20260809_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_documents",
        sa.Column(
            "author_identities",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_source_documents_author_identities",
        "source_documents",
        ["author_identities"],
        postgresql_using="gin",
    )
    op.create_table(
        "connector_sync_states",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("rate_limit_remaining", sa.Integer(), nullable=True),
        sa.Column("rate_limit_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('never_synced', 'running', 'succeeded', 'failed')",
            name=op.f("ck_connector_sync_states_valid_sync_status"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_connector_sync_states_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_connector_sync_states"),
        sa.UniqueConstraint(
            "project_id",
            "source_type",
            name="uq_connector_sync_states_project_id",
        ),
    )
    op.create_index(
        "ix_connector_sync_states_project_id",
        "connector_sync_states",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_connector_sync_states_project_id", table_name="connector_sync_states"
    )
    op.drop_table("connector_sync_states")
    op.drop_index(
        "ix_source_documents_author_identities", table_name="source_documents"
    )
    op.drop_column("source_documents", "author_identities")
