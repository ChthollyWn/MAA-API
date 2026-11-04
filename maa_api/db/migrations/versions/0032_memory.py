"""M79：长期记忆 ``memory_item``（v3 specs/m79 §4.1、v3 docs/03 §10.2）。

纯新增表，不碰既有表、不回填数据。逐列照 03 §10.2：

- 四个关联字段都是 ``SET NULL``：工单、会话、运行或被取代的旧记忆被清理时，
  记忆本身仍要留着（03 §14 不自动删）；
- ``supersedes_id`` 是自引用；``content`` 用 ``sa.Text()``；
- ``confidence`` 可空（``remember`` 记 1，抽取的候选用模型给的置信度）；
- 索引只有 ``ix_memory_item_status_kind`` 与 ``ix_memory_item_subject``；
  spec §4.1 写的 ``updated_at`` 索引按 03 §10.2 不建。

``downgrade`` 删两条索引与表；空库可以升降，已有数据随表删除。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_memory"
down_revision: Union[str, Sequence[str], None] = "0031_skills"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_item",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_ticket_id", sa.String(length=36), nullable=True),
        sa.Column("source_session_id", sa.String(length=36), nullable=True),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=32), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_ticket_id"],
            ["ticket.id"],
            name="fk_memory_item_source_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_session_id"],
            ["agent_session.id"],
            name="fk_memory_item_source_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["agent_run.id"],
            name="fk_memory_item_source_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["memory_item.id"],
            name="fk_memory_item_supersedes_id_memory_item",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_item"),
    )
    op.create_index(
        "ix_memory_item_status_kind",
        "memory_item",
        ["status", "kind"],
    )
    op.create_index(
        "ix_memory_item_subject",
        "memory_item",
        ["subject"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_item_subject", table_name="memory_item")
    op.drop_index("ix_memory_item_status_kind", table_name="memory_item")
    op.drop_table("memory_item")
