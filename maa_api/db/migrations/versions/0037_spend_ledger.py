"""M84：每日消耗账本 ``spend_ledger``（v3 specs/m84 §4.1、docs/v3/03 §11.6）。

纯新增、不回填：一张新表，9 列逐个照 03 §11.6 —— ``id`` 整数自增主键，
``pipeline_id`` / ``task_id`` / ``ticket_id`` 分别外键到 ``pipeline`` / ``task`` /
``ticket`` 且 ``ON DELETE SET NULL``（主体被清理时账本行留着，用量汇总不断），
``kind`` 存 ``SpendKind`` 的三个取值、``source`` 存 ``PipelineSource`` 的取值，
``planned`` 是参数上限、``actual`` 可空（M84 不写）；``created_at`` 用于按消耗日
（北京时间 04:00 分日）窗口求和。

- 一条索引 ``ix_spend_ledger_kind_created_at``：按种类在时间窗内汇总用量。
- 自增依赖 ``sqlite_autoincrement=True`` 才会在 DDL 里落下 ``AUTOINCREMENT``
  （docs/04 §3.1，与 ``watchdog_alert`` 同一写法）。
- 历史流水线不补行：本迁移之前没有任何消耗记录，凭空回填只会把旧流水线算进
  上线当天的用量。
- 降级删表，账本属于可丢弃的运行数据（03 §14 未列保留策略，不自动清理）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_spend_ledger"
down_revision: Union[str, Sequence[str], None] = "0036_watchdog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index("ix_spend_ledger_kind_created_at", table_name="spend_ledger")
    op.drop_table("spend_ledger")
