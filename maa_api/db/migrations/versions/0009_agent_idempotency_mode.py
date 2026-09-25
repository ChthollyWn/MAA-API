"""Remember Agent invoke mode for crash-safe idempotency response recovery."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0009_agent_idempotency_mode"
down_revision: Union[str, Sequence[str], None] = "0008_agent_invoke_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_idempotency",
        sa.Column("request_mode", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_idempotency", "request_mode")
