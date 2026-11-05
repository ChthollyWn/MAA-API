"""M85：IM 通道三表 ``channel_binding`` / ``channel_bind_code`` / ``outbox``
（v3 specs/m85 §4.1、docs/v3/03 §11.7）。

- 纯新增，不回填：三张表逐列照 03 §11.7。
- ``channel_binding``：36 字符 UUID 主键；``status`` 只存 ``active`` / ``revoked``
  两个字符串；唯一索引 ``uq_channel_binding_platform_external_user_id_external_chat_id``
  保证同一平台上的同一用户与同一会话只有一行绑定。
- ``channel_bind_code``：``code`` 是 16 字符主键，``platform`` 可空（为空表示不限
  平台，两边都非空时才比对），``expires_at`` 必填、``used_at`` 空表示尚未使用。
- ``outbox``：整数自增主键，``dedupe_key`` 唯一约束（重复入队返回旧行），
  ``payload`` / ``receipt`` 用 JSON，``last_error`` 用 TEXT；``ref_run_id`` /
  ``ref_ticket_id`` 外键到 ``agent_run`` / ``ticket`` 且 ``ON DELETE SET NULL``
  （主体清理后发件箱行留着，供审计与续发）；``status + next_attempt_at`` 建索引
  服务每 2 秒的取件查询。
- 自增依赖 ``sqlite_autoincrement=True`` 才会在 DDL 里落下 ``AUTOINCREMENT``
  （docs/04 §3.1，与 ``watchdog_alert`` 同一写法）。
- 降级反序删表：先 ``outbox``，再 ``channel_bind_code``，最后 ``channel_binding``；
  三张表都属于可丢弃的运行数据（03 §14 的保留策略在本迁移不实现）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_channels"
down_revision: Union[str, Sequence[str], None] = "0037_spend_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "channel_binding",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("external_user_id", sa.String(length=128), nullable=False),
        sa.Column("external_chat_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("bound_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_channel_binding"),
    )
    op.create_index(
        "uq_channel_binding_platform_external_user_id_external_chat_id",
        "channel_binding",
        ["platform", "external_user_id", "external_chat_id"],
        unique=True,
    )
    op.create_table(
        "channel_bind_code",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_channel_bind_code"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=24), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("receipt", sa.JSON(), nullable=True),
        sa.Column("ref_run_id", sa.String(length=36), nullable=True),
        sa.Column("ref_ticket_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ref_run_id"],
            ["agent_run.id"],
            name="fk_outbox_ref_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ref_ticket_id"],
            ["ticket.id"],
            name="fk_outbox_ref_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox"),
        sa.UniqueConstraint("dedupe_key", name="uq_outbox_dedupe_key"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_outbox_status_next_attempt_at", "outbox", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_status_next_attempt_at", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("channel_bind_code")
    op.drop_index(
        "uq_channel_binding_platform_external_user_id_external_chat_id",
        table_name="channel_binding",
    )
    op.drop_table("channel_binding")
