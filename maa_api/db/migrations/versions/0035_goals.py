"""M82：目标两表与 ``ticket.goal_id``（v3 specs/m82 §4.1、docs/v3/03 §11.4）。

- 两张新表：``goal``（长程目标）与 ``goal_milestone``（里程碑，``ord`` 从 1 开始）。
- 已有表加一列：``ticket.goal_id`` → ``goal.id``，``ON DELETE SET NULL``。它与
  ``goal.planning_ticket_id`` → ``ticket.id`` 构成循环外键，处理方式与 M69 的
  ``agent_session.ticket_id`` ↔ ``ticket.worker_session_id``、M81 的
  ``ticket.source_trigger_id`` ↔ ``agent_trigger.ticket_id`` 一致：模型里双向声明，
  迁移里先建 ``goal``，再用 batch 给 ``ticket`` 加列与外键。
- SQLite 不能用 ALTER 加约束，``ticket`` 只能 batch 重建；离线 ``--sql`` 模式没有
  连接可反射，所以显式给 ``copy_from``（与 0021、0026、0033、0034 同一写法）。
  ``copy_from`` 必须与 batch 开始时的表一致：upgrade 时有 M81 加的
  ``source_trigger_id`` 及其外键、没有 ``goal_id``；downgrade 时两者都在。
  ``ticket`` 原有的两条索引也要声明，否则 batch 重建后无处可删。
- 降级会删掉全部目标与里程碑，这是 spec §4.1 的约定：关联的养成计划与定时任务
  不受影响（``goal.upgrade_plan_id`` / ``schedule_ids`` 只是引用），用户可以在
  定时任务页手动处理；``ticket`` 自己的数据不丢。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_goals"
down_revision: Union[str, Sequence[str], None] = "0034_triggers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 与 maa_api/db/models.py 文件头的命名约定逐字一致：重建出的约束名要和模型对得上。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _metadata() -> sa.MetaData:
    """copy_from 用的元数据；外键目标表只需要主键列。"""
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    sa.Table("agent_session", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("agent_trigger", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("goal", metadata, sa.Column("id", sa.String(36), primary_key=True))
    return metadata


def _ticket_table(*, with_goal: bool) -> sa.Table:
    """``ticket`` 在本迁移视角的定义，只给 batch 的 copy_from 用（照 0034_triggers）。

    列、外键、索引逐项照 ``db/models.py`` 的 Ticket 抄；``source_trigger_id`` 与它
    的外键是 M81 加的，本迁移必须保留。``with_goal=True`` 时多 ``goal_id`` 与它的
    外键（downgrade 的 batch 开始时两列都在）。
    """
    metadata = _metadata()
    columns = [
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "orchestrator_session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "worker_session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("last_report_status", sa.String(16), nullable=True),
        sa.Column("human_action", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column(
            "source_trigger_id",
            sa.Integer(),
            sa.ForeignKey("agent_trigger.id", ondelete="SET NULL"),
            nullable=True,
        ),
    ]
    if with_goal:
        columns.append(
            sa.Column(
                "goal_id",
                sa.String(36),
                sa.ForeignKey("goal.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    table = sa.Table("ticket", metadata, *columns, sa.PrimaryKeyConstraint("id"))
    sa.Index("ix_ticket_status_updated_at", table.c.status, table.c.updated_at)
    sa.Index("ix_ticket_kind_created_at", table.c.kind, table.c.created_at)
    return table


def upgrade() -> None:
    # 列逐个照模型写：str(n)→sa.String(length=n)，int→sa.Integer()，
    # JSON→sa.JSON()，TEXT→sa.Text()，datetime→sa.DateTime()；可空性照模型；
    # target / progress 带 server_default=sa.text("'{}'")，schedule_ids 带 "'[]'"。
    op.create_table(
        "goal",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "target",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("planning_ticket_id", sa.String(length=36), nullable=True),
        sa.Column("upgrade_plan_id", sa.String(length=36), nullable=True),
        sa.Column(
            "schedule_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("review_cron", sa.String(length=64), nullable=False),
        sa.Column("last_review_at", sa.DateTime(), nullable=True),
        sa.Column(
            "progress",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["planning_ticket_id"],
            ["ticket.id"],
            name="fk_goal_planning_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["upgrade_plan_id"],
            ["upgrade_plan.id"],
            name="fk_goal_upgrade_plan_id_upgrade_plan",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_goal"),
    )
    op.create_index("ix_goal_status_updated_at", "goal", ["status", "updated_at"])

    op.create_table(
        "goal_milestone",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("goal_id", sa.String(length=36), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "metrics",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goal.id"],
            name="fk_goal_milestone_goal_id_goal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_goal_milestone"),
    )
    op.create_index(
        "ix_goal_milestone_goal_id_ord", "goal_milestone", ["goal_id", "ord"]
    )

    with op.batch_alter_table(
        "ticket", copy_from=_ticket_table(with_goal=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("goal_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_ticket_goal_id_goal",
            "goal",
            ["goal_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "ticket", copy_from=_ticket_table(with_goal=True)
    ) as batch_op:
        batch_op.drop_constraint("fk_ticket_goal_id_goal", type_="foreignkey")
        batch_op.drop_column("goal_id")

    op.drop_index("ix_goal_milestone_goal_id_ord", table_name="goal_milestone")
    op.drop_table("goal_milestone")
    op.drop_index("ix_goal_status_updated_at", table_name="goal")
    op.drop_table("goal")
