"""Add the M29 ``copilot_grant`` table (spec §9.6, appendix E).

作业授权是**纯新增**：``copilot_grant`` 记录「哪个会话、哪一关、几次尝试、什么成功标准、
到什么时候有效」，由 ``services/copilot/grant.py`` 的状态机读写。
**不需要任何回填**：旧库里没有作业授权，页面对应「未授权」。

一条表 + 三条索引：

- ``uq_copilot_grant_active_session``：``(session_id) WHERE status = 'active'`` 的**部分
  唯一索引** —— 规格 §9.6「每个会话同时最多一个 ``active``」由数据库兜底，并发批准
  不会留下两条有效授权（终态历史行不受限，与 ``uq_update_record_running_target``
  同一手法）。
- ``ix_copilot_grant_status_expires_at``：服务重启把 ``status='active'`` 标
  ``interrupted``（``mark_stale_active``）与「有效期到了」的判定都走这条。
- ``ix_copilot_grant_session_id_created_at``：会话维度的授权历史与
  ``GET /api/agent/sessions/{id}`` 里的 ``copilot_grant`` 条。

``session_id`` 用 ``ON DELETE SET NULL``（与 ``upgrade_plan.session_id`` 同口径）：
会话删了，授权记录仍要留着（审计与「这个会话做过什么」），只是不再对任何会话生效。

``attempts`` / ``used`` 是附录 E ``attempts_total`` / ``attempts_used`` 的简称，
落地口径见 ``models.CopilotGrant`` 的 docstring。

downgrade 只删索引与表：授权是运行时状态，删掉即回到「没有授权」。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_copilot_grant"
down_revision: Union[str, Sequence[str], None] = "0016_account_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "copilot_grant",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("stage_id", sa.String(length=64), nullable=False),
        sa.Column("stage_code", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("success", sa.String(length=16), nullable=False),
        sa.Column("support", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_copilot_grant_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_copilot_grant"),
    )
    op.create_index(
        "uq_copilot_grant_active_session",
        "copilot_grant",
        ["session_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_copilot_grant_status_expires_at",
        "copilot_grant",
        ["status", "expires_at"],
    )
    op.create_index(
        "ix_copilot_grant_session_id_created_at",
        "copilot_grant",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_copilot_grant_session_id_created_at", table_name="copilot_grant"
    )
    op.drop_index("ix_copilot_grant_status_expires_at", table_name="copilot_grant")
    op.drop_index("uq_copilot_grant_active_session", table_name="copilot_grant")
    op.drop_table("copilot_grant")
