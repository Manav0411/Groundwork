"""Add Slack channel mapping.

Revision ID: 20260824_0004
Revises: 20260816_0003
"""

import sqlalchemy as sa
from alembic import op

revision = "20260824_0004"
down_revision = "20260816_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "slack_channel_ids",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "slack_channel_ids")
