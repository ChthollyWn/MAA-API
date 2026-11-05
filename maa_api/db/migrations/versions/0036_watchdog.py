"""M83：值守告警表 ``watchdog_alert``（v3 specs/m83 §4.1、docs/v3/03 §11.5）。

- 一张新表，列逐个照 03 §11.5：``id`` 整数自增主键，``message`` 用 TEXT，
  ``evidence`` 用 JSON 且默认 ``{}``，``resolved_at`` 可空。
- 两条索引：``ix_watchdog_alert_created_at``（告警列表按时间倒序）与
  ``ix_watchdog_alert_target_kind_target_id``（未解除告警按目标查重）。
- 去重语义在服务层做（spec §4.1：同一 ``check`` + ``target_kind`` + ``target_id``
  在 ``resolved_at IS NULL`` 时不插第二行），不建部分唯一索引：sqlite 的
  ``resolved_at`` 历史行要允许同目标重复出现，而部分唯一索引会把它一起挡住。
- 自增依赖 ``sqlite_autoincrement=True`` 才会在 DDL 里落下 ``AUTOINCREMENT``
  （docs/04 §3.1，与 ``agent_message`` 同一写法）；``create_table`` 的
  ``autoincrement`` 列参数只影响 SQLAlchemy 的 INSERT 行为，不写 SQLite 关键字。
- 降级删表，告警属于可丢弃的运行日志（03 §14 保留 90 天）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_watchdog"
down_revision: Union[str, Sequence[str], None] = "0035_goals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchdog_alert",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("check", sa.String(length=48), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column("action_status", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_watchdog_alert"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_watchdog_alert_created_at", "watchdog_alert", ["created_at"])
    op.create_index(
        "ix_watchdog_alert_target_kind_target_id",
        "watchdog_alert",
        ["target_kind", "target_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_watchdog_alert_target_kind_target_id", table_name="watchdog_alert"
    )
    op.drop_index("ix_watchdog_alert_created_at", table_name="watchdog_alert")
    op.drop_table("watchdog_alert")
