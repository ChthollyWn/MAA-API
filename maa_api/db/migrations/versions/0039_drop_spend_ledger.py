"""维护性补丁：删除每日消耗账本 ``spend_ledger``（v3 specs/remove-spend-legacy-sessions §4.1）。

消耗护栏（M84）整体删除，账本表与索引一并移除；表里的历史数据不保留、不回填
其它表（spec §8 回滚一节：降级只恢复表结构，不恢复已删数据）。

- ``upgrade``：先删索引 ``ix_spend_ledger_kind_created_at``，再删表
  ``spend_ledger``；``0037_spend_ledger.py`` 与迁移链本身不动（历史记录保留）。
- ``downgrade``：按 0037 的 DDL 原样重建表、三条 ``SET NULL`` 外键、主键约束与
  索引（名称、列序、``sqlite_autoincrement`` 全部一致），保证可回滚到 0038 的
  schema 形状。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_drop_spend_ledger"
down_revision: Union[str, Sequence[str], None] = "0038_channels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_spend_ledger_kind_created_at", table_name="spend_ledger")
    op.drop_table("spend_ledger")


def downgrade() -> None:
    op.create_table(
        "spend_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("planned", sa.Integer(), nullable=False),
        sa.Column("actual", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipeline.id"],
            name="fk_spend_ledger_pipeline_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_spend_ledger_task_id_task",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_spend_ledger_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_spend_ledger"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_spend_ledger_kind_created_at", "spend_ledger", ["kind", "created_at"]
    )
