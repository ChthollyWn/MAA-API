"""Persist request correlation and 24-hour Agent invoke idempotency."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0008_agent_invoke_idempotency"
down_revision: Union[str, Sequence[str], None] = "0007_agent_callback_statistics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_audit",
        sa.Column("request_id", sa.String(length=128), nullable=True),
    )
    op.create_table(
        "agent_idempotency",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("caller", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("audit_id", sa.Integer(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["agent_audit.id"],
            name="fk_agent_idempotency_audit_id_agent_audit",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_idempotency"),
        sa.UniqueConstraint(
            "caller", "key", name="uq_agent_idempotency_caller_key"
        ),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_agent_idempotency_created_at",
        "agent_idempotency",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_idempotency_created_at", table_name="agent_idempotency")
    op.drop_table("agent_idempotency")
    op.drop_column("agent_audit", "request_id")
