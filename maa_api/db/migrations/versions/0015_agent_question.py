"""Add the ``agent_question`` table for M21 agent questions.

Agent 主动提问是**新增**的一等交互：模型调 ``ask_user`` 发一张带选项的卡片，本轮
挂起等用户点选或跳过，10 分钟无人应答就过期并结束本轮（规格 §3.1–§3.3）。它与
``confirmation`` 表无关，也不复用那条链路，所以升级只是新增一张表、两个索引，
**不需要任何回填**：历史会话里没有提问，旧库里的 pending 概念只属于 confirmation。

两个索引各服务一条固定查询（docs/04 §6）：``(status, expires_at)`` 给后台超时扫描
（``status='pending' AND expires_at < now``），``(session_id, status, created_at)``
给「本会话待答卡」列表。``audit_id`` 用 ``ON DELETE SET NULL``（审计行被清理时提问
本身仍要在历史里可见），``session_id`` 用 ``ON DELETE CASCADE``（会话删了，它的
提问没有独立存在的意义，与 ``agent_message`` 同口径）。

downgrade 只删表：表里的行都是「一次提问 + 用户的裁决」，删掉即回到没有提问的旧行为。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_agent_question"
down_revision: Union[str, Sequence[str], None] = "0014_agent_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_question",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("tool_call_id", sa.String(length=64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("multiple", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=True),
        sa.Column("answered_by", sa.String(length=32), nullable=True),
        sa.Column("audit_id", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_agent_question_session_id_agent_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["agent_audit.id"],
            name="fk_agent_question_audit_id_agent_audit",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_question"),
    )
    op.create_index(
        "ix_agent_question_status_expires_at",
        "agent_question",
        ["status", "expires_at"],
    )
    op.create_index(
        "ix_agent_question_session_status_created_at",
        "agent_question",
        ["session_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_question_session_status_created_at", table_name="agent_question")
    op.drop_index("ix_agent_question_status_expires_at", table_name="agent_question")
    op.drop_table("agent_question")
