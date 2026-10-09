"""Add the Web Push subscription table for M14.

订阅按 endpoint 唯一：浏览器对同一个 Service Worker 重复订阅拿到的是同一个
endpoint，重放注册必须更新既有行而不是新增。失效订阅保留行并置
``disabled_at``（推送服务返回 404/410 时），而不是删除：用户需要看到
「这台设备的订阅已失效」并有机会重新启用。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_push_subscription"
down_revision: Union[str, Sequence[str], None] = "0011_agent_incident_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscription",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("endpoint_host", sa.String(length=255), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=255), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),
    )
    op.create_index(
        "ix_push_subscription_disabled_at",
        "push_subscription",
        ["disabled_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_push_subscription_disabled_at", table_name="push_subscription")
    op.drop_table("push_subscription")
