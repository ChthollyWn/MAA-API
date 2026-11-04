"""M75：产物与计划表 ``artifact`` / ``run_plan``（v3 M75 规格 §4.1、docs/03 §8.2/§8.3）。

纯新增表，不碰既有表、不回填数据。

- ``artifact``：三个关联字段都 ``ON DELETE SET NULL``（产物比会话、工单与运行活得
  久）；``content`` 用 ``sa.Text()``（小正文直接进库），``meta`` 是 JSON ``{}``；
  两条按时间的复合索引分别服务工单详情与会话回放。
- ``run_plan``：``session_id`` 用 ``ON DELETE CASCADE``（会话删了计划没有独立存在的
  意义），``ticket_id`` 用 ``ON DELETE SET NULL``；``items`` 是 JSON ``[]``；一条部分
  唯一索引保证同一会话最多一条未结束计划（``draft`` / ``awaiting_approval`` /
  ``active``），终态历史行不受限。

downgrade 按依赖反序删表：先 ``run_plan`` 再 ``artifact``，既有数据不受影响。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_artifacts_plans"
down_revision: Union[str, Sequence[str], None] = "0028_compaction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "artifact",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("ticket_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_artifact_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_artifact_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_run.id"],
            name="fk_artifact_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact"),
    )
    op.create_index(
        "ix_artifact_ticket_id_created_at",
        "artifact",
        ["ticket_id", "created_at"],
    )
    op.create_index(
        "ix_artifact_session_id_created_at",
        "artifact",
        ["session_id", "created_at"],
    )

    op.create_table(
        "run_plan",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("approved_by", sa.String(length=32), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_run_plan_session_id_agent_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_run_plan_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_plan"),
    )
    op.create_index(
        "uq_run_plan_session_id_active",
        "run_plan",
        ["session_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('draft','awaiting_approval','active')"),
    )


def downgrade() -> None:
    op.drop_index("uq_run_plan_session_id_active", table_name="run_plan")
    op.drop_table("run_plan")
    op.drop_index("ix_artifact_session_id_created_at", table_name="artifact")
    op.drop_index("ix_artifact_ticket_id_created_at", table_name="artifact")
    op.drop_table("artifact")
