"""Record the standalone question a follow-up was answered as.

Nullable because a self-contained question has no resolved form, which is the common case.

Revision ID: 20260825_0005
Revises: 20260824_0004
"""

import sqlalchemy as sa
from alembic import op

revision = "20260825_0005"
down_revision = "20260824_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("query_runs", sa.Column("resolved_query", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("query_runs", "resolved_query")
