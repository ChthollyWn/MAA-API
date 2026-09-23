"""Persist pipeline device-preflight deferrals.

Revision ID: 0003_pipeline_device_deferral
Revises: 0002_migrate_daily_task_json
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003_pipeline_device_deferral"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable due time and a zero-default deferral counter."""
    op.add_column(
        "pipeline",
        sa.Column("deferred_until", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "pipeline",
        sa.Column(
            "defer_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Remove the deferral state while retaining existing pipeline rows."""
    with op.batch_alter_table("pipeline", recreate="always") as batch_op:
        batch_op.drop_column("defer_count")
        batch_op.drop_column("deferred_until")
