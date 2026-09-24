"""Persist named API-console requests.

Revision ID: 0005_api_snippet
Revises: 0004_fix_schedule_weekday
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005_api_snippet"
down_revision: Union[str, Sequence[str], None] = "0004_fix_schedule_weekday"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_snippet",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("path_params", sa.JSON(), nullable=False),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("body", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_api_snippet"),
        sa.UniqueConstraint("name", name="uq_api_snippet_name"),
    )


def downgrade() -> None:
    op.drop_table("api_snippet")
