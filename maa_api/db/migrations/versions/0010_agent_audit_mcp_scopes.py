"""Persist the MCP scope snapshot on agent audit rows."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_agent_audit_mcp_scopes"
down_revision: Union[str, Sequence[str], None] = "0009_agent_idempotency_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_audit",
        sa.Column("scopes", sa.JSON(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_audit", "scopes")
