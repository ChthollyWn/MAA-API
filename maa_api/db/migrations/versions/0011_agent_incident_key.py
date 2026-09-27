"""Persist one Agent session per automatically diagnosed failure event."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0011_agent_incident_key"
down_revision: Union[str, Sequence[str], None] = "0010_agent_audit_mcp_scopes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column("incident_key", sa.String(length=160), nullable=True),
    )
    op.create_index(
        "uq_agent_session_incident_key",
        "agent_session",
        ["incident_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_agent_session_incident_key", table_name="agent_session")
    op.drop_column("agent_session", "incident_key")
