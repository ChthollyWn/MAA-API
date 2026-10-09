"""Add the ``agent_knowledge`` table for the M18 knowledge base.

内置知识随代码发布（``maa_api/agent/knowledge/builtin/*.md``），这张表只存
用户的改动：``id`` 与内置条目相同的行是覆盖行，只写与内置**不同**的列，其余
列为 NULL，读取时继承内置值；其余行是自定义条目，所有列必填。因此升级不需要
任何回填：表里没有某一行就意味着「用内置原文」，新版本改内置内容后，用户没
覆盖过的字段自动跟随新版本。

`kind` 可空是覆盖语义的一部分：内置条目的类型不可覆盖，覆盖行里这一列恒为
NULL。`enabled` 非空、默认真（Python 侧默认值，与 ``schedule`` /
``notify_channel`` / ``resource_asset`` 的 ``enabled`` 同口径，DDL 不带
server_default）。

downgrade 只删表；表里的行都是用户对内置内容的覆盖，删掉即回到内置默认值。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_agent_knowledge"
down_revision: Union[str, Sequence[str], None] = "0013_agent_message_reasoning"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_knowledge",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=True),
        sa.Column("title", sa.String(length=32), nullable=True),
        sa.Column("when_to_use", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("agent_knowledge")
