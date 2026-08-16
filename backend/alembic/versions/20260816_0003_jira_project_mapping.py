"""Add Jira project mapping.

Revision ID: 20260816_0003
Revises: 20260810_0002
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0003"
down_revision: str | None = "20260810_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("jira_project_key", sa.Text(), nullable=True))
    op.create_index(
        "ix_projects_jira_project_key",
        "projects",
        ["jira_project_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_projects_jira_project_key", table_name="projects")
    op.drop_column("projects", "jira_project_key")
