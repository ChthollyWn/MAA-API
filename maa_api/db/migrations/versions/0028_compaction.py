"""M74：压缩检查点表 ``compaction_checkpoint``（v3 M74 规格 §4.1）。

纯新增表，不碰既有表、不回填数据：检查点只由 M74 之后的压缩写入，历史会话没有
检查点等于「全量历史 + 字符预算」的老路径，不需要补行。

- ``session_id`` 用 ``ON DELETE CASCADE``：会话删了，它的检查点没有独立存在的意义；
  ``run_id`` 用 ``ON DELETE SET NULL``：产出摘要的运行被清理时检查点仍要留着。
- ``summary`` 用 ``sa.Text()``（渲染后的摘要长度不定），``summary_json`` /
  ``durable`` 用 ``sa.JSON()``（结构性摘要与压缩时收集的 durable 状态）。
- 一条索引 ``ix_compaction_checkpoint_session_id_created_at``：按会话取检查点列表
  时 ``WHERE session_id = ? ORDER BY created_at DESC``。
- 「一个会话最多一个 active」由 ``Compactor`` 在同一事务里先置旧的为
  ``superseded`` 再插新行来保证，不加 03 没有的唯一索引。

downgrade 只删索引与这张表：``agent_session`` / ``agent_run`` 等既有数据不受影响。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_compaction"
down_revision: Union[str, Sequence[str], None] = "0027_device_lease"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "compaction_checkpoint",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("upto_seq", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("durable", sa.JSON(), nullable=False),
        sa.Column("tokens_before", sa.Integer(), nullable=False),
        sa.Column("tokens_after", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_compaction_checkpoint_session_id_agent_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_run.id"],
            name="fk_compaction_checkpoint_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_compaction_checkpoint"),
    )
    op.create_index(
        "ix_compaction_checkpoint_session_id_created_at",
        "compaction_checkpoint",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_compaction_checkpoint_session_id_created_at",
        table_name="compaction_checkpoint",
    )
    op.drop_table("compaction_checkpoint")
