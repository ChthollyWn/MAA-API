"""Index API-console snippet ordering."""

from typing import Sequence, Union

from alembic import op


revision: str = "0006_api_snippet_updated_at_index"
down_revision: Union[str, Sequence[str], None] = "0005_api_snippet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_api_snippet_updated_at", "api_snippet", ["updated_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_api_snippet_updated_at", table_name="api_snippet")
