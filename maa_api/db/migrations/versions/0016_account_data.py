"""Add the five M27 account-data tables (spec §7.3, appendix E).

账号数据是**纯新增**：``domain/task.py`` 多了 ``OperBox`` / ``Depot`` 两个任务类型，
它们的 ``SubTaskExtraInfo`` 回调把识别结果落到这五张表里，因此升级只是建表 + 索引，
**不需要任何回填**：旧库里没有识别记录，也没有账号干员/仓库数据，页面对应「尚未识别」。

五张表与索引（docs/04 §6 的「等值 → 范围 → 排序」列序）：

- ``account_operator``：一行一名干员，``char_id`` 主键。同时放识别值、手动编辑值、
  各自的来源（``field_sources``）与最近一次识别值（``recognized_values``），合并规则
  见规格 §7.3。表最多几百行（国服干员总数），全表读 + 内存筛选，因此不建索引。
- ``account_operator_diff``：识别覆盖手动值的差异。``(dismissed_at, created_at)`` 服务
  「待处理差异按时间列出」这一条查询（``dismissed_at IS NULL``）。
- ``account_depot_item``：``item_id`` 主键，整表替换。同样只有几百行，不建索引。
- ``account_recognition``：一次识别一条（时间、流水线、任务、渠道、数量）。
  ``(kind, recognized_at)`` 服务「最近一次 operbox / depot 识别」。
- ``upgrade_plan``：养成计划，``(status, created_at)`` 服务计划列表。

两处字段比附录 E 多，都是页面与工具已经在用的：``account_operator.profession``
（列表与工具的 profession 筛选）与 ``account_depot_item.category``（仓库页按分类
分组、``get_depot`` 的 category 筛选）；识别回调本身给不出职业，由 capture 层用本机
``battle_data.json`` 补。``account_recognition.pipeline_id`` / ``task_id`` 用
``ON DELETE SET NULL``（与 ``stage_drop`` 同口径：流水线删了，识别记录仍要留着），
``upgrade_plan.schedule_id`` / ``session_id`` 同样 SET NULL（计划比定时任务活得久）。

downgrade 只删表与索引：五张表都是账号数据的快照，删掉即回到「尚未识别」，
没有别的表引用它们（``account_recognition`` 指向 pipeline / task 是出边）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_account_data"
down_revision: Union[str, Sequence[str], None] = "0015_agent_question"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "account_operator",
        sa.Column("char_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("rarity", sa.Integer(), nullable=True),
        sa.Column("profession", sa.String(length=16), nullable=True),
        sa.Column("own", sa.Boolean(), nullable=False),
        sa.Column("elite", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("potential", sa.Integer(), nullable=False),
        sa.Column("skill_level", sa.Integer(), nullable=False),
        sa.Column("masteries", sa.JSON(), nullable=False),
        sa.Column("modules", sa.JSON(), nullable=False),
        sa.Column("recognized_values", sa.JSON(), nullable=True),
        sa.Column("field_sources", sa.JSON(), nullable=False),
        sa.Column("recognized_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("char_id", name="pk_account_operator"),
    )
    op.create_table(
        "account_operator_diff",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("char_id", sa.String(length=64), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("manual_value", sa.JSON(), nullable=True),
        sa.Column("recognized_value", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_account_operator_diff"),
    )
    op.create_index(
        "ix_account_operator_diff_dismissed_at_created_at",
        "account_operator_diff",
        ["dismissed_at", "created_at"],
    )
    op.create_table(
        "account_depot_item",
        sa.Column("item_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=True),
        sa.Column("recognized_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("item_id", name="pk_account_depot_item"),
    )
    op.create_table(
        "account_recognition",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("client_type", sa.String(length=16), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("recognized_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipeline.id"],
            name="fk_account_recognition_pipeline_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_account_recognition_task_id_task",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_account_recognition"),
    )
    op.create_index(
        "ix_account_recognition_kind_recognized_at",
        "account_recognition",
        ["kind", "recognized_at"],
    )
    op.create_table(
        "upgrade_plan",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=False),
        sa.Column("requirements", sa.JSON(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("schedule_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedule.id"],
            name="fk_upgrade_plan_schedule_id_schedule",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_upgrade_plan_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_upgrade_plan"),
    )
    op.create_index(
        "ix_upgrade_plan_status_created_at", "upgrade_plan", ["status", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_upgrade_plan_status_created_at", table_name="upgrade_plan")
    op.drop_table("upgrade_plan")
    op.drop_index(
        "ix_account_recognition_kind_recognized_at", table_name="account_recognition"
    )
    op.drop_table("account_recognition")
    op.drop_table("account_depot_item")
    op.drop_index(
        "ix_account_operator_diff_dismissed_at_created_at",
        table_name="account_operator_diff",
    )
    op.drop_table("account_operator_diff")
    op.drop_table("account_operator")
