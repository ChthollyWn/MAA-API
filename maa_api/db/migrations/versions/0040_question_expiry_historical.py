"""维护性补丁：提问不再有有效期，``agent_question.expires_at`` 改为历史列。

提问的时间失效整体删除（v3 specs/questions-no-expiry §4.4）：新提问不写
``expires_at``，服务也不再按它判过期。列本身保留用于回放旧数据，因此只放宽
可空约束并删掉只为超时扫描存在的复合索引，不删列、不清理历史值。

- ``upgrade``：删 ``ix_agent_question_status_expires_at``（提问不再过期，索引与
  扫描一起退休），再用 ``batch_alter_table`` 把 ``expires_at`` 改为可空。
  SQLite 的 batch 重建要按 ``copy_from`` 声明表；离线 ``upgrade --sql`` 没有连接
  可反射，必须显式给全列与保留的索引，否则渲染不出 SQL。
- ``downgrade``：先用 ``created_at`` 回填、恢复非空约束，再建回原索引；旧提问的
  原始过期时刻无法恢复，降级后统一等于创建时间。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_question_expiry_historical"
down_revision: Union[str, Sequence[str], None] = "0039_drop_spend_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _question_table() -> sa.Table:
    """本迁移视角的 ``agent_question``：batch 重建的 ``copy_from``，含保留的索引。"""
    return sa.Table(
        "agent_question",
        sa.MetaData(),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(length=36),
            sa.ForeignKey("agent_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("multiple", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=True),
        sa.Column("answered_by", sa.String(length=32), nullable=True),
        sa.Column(
            "audit_id",
            sa.Integer(),
            sa.ForeignKey("agent_audit.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column(
            "origin_run_id",
            sa.String(length=36),
            sa.ForeignKey("agent_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "origin_ticket_id",
            sa.String(length=36),
            sa.ForeignKey("ticket.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("origin_agent", sa.String(length=32), nullable=True),
        sa.Index(
            "ix_agent_question_session_status_created_at",
            "session_id",
            "status",
            "created_at",
        ),
    )


def upgrade() -> None:
    op.drop_index(
        "ix_agent_question_status_expires_at", table_name="agent_question"
    )
    with op.batch_alter_table(
        "agent_question", copy_from=_question_table()
    ) as batch_op:
        batch_op.alter_column(
            "expires_at",
            existing_type=sa.DateTime(),
            nullable=True,
        )


def downgrade() -> None:
    # 升级只放宽了可空约束、没有删列；降级要先回填再在同一次 batch 重建里恢复非空。
    op.execute(
        "UPDATE agent_question SET expires_at = created_at WHERE expires_at IS NULL"
    )
    with op.batch_alter_table(
        "agent_question", copy_from=_question_table()
    ) as batch_op:
        batch_op.alter_column(
            "expires_at",
            existing_type=sa.DateTime(),
            nullable=False,
        )
    op.create_index(
        "ix_agent_question_status_expires_at",
        "agent_question",
        ["status", "expires_at"],
    )
