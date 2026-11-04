"""M78：技能覆盖层 ``agent_skill``，把 9 条 playbook 的覆盖行搬出知识库。

v2 把「怎么做」写成 ``kind=playbook`` 的知识条目，用户对它们的改动存在
``agent_knowledge`` 的覆盖行里；v3 把这些内容改成技能（V3-D44），覆盖层跟着搬进
``agent_skill``：

- ``when_to_use`` → ``description``（``agent_skill.description`` 放宽到 200）；
- ``kind`` 丢弃：技能没有「种类」，来历由 ``origin='override'`` 表达；
- ``tools`` / ``agents`` 由内置 ``SKILL.md`` 提供，覆盖行里为 NULL，读取时继承；
- ``status`` 固定为 ``active``，``enabled`` 原样保留（只停用的覆盖行仍然有效）。

只搬 ``MIGRATED_IDS`` 这 9 个 id：其余 ``agent_knowledge`` 行（内置条目的覆盖与
用户自建条目）仍归知识库。搬走以后表里没有这 9 个 id 的行，知识库的 ``_merge``
不会遇到「内置已删但覆盖行残缺」的情况，因此**不**走 M30 的
``migrate_deleted_overrides``。

``downgrade`` 只能还原 9 条的覆盖行（``kind`` 回 NULL，``description`` 截回
``when_to_use`` 的 120 字上限）；用户技能与生成的草稿随表删除，不可还原。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_skills"
down_revision: Union[str, Sequence[str], None] = "0030_knowledge_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: v2 里 9 条 playbook 知识条目的 id；只有它们的覆盖行搬成技能覆盖行。
MIGRATED_IDS = (
    "account-resources",
    "playbook-daily-weekly",
    "playbook-drop-lookup",
    "playbook-infrast-shift",
    "playbook-promotion-planning",
    "playbook-recruit",
    "playbook-sanity-planning",
    "playbook-stage-clear",
    "task-preconditions",
)
_IN = ", ".join(f"'{i}'" for i in MIGRATED_IDS)


def upgrade() -> None:
    op.create_table(
        "agent_skill",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=32), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("agents", sa.JSON(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_skill"),
    )
    # 9 条 playbook 的覆盖行搬成技能覆盖行：when_to_use → description，kind 丢弃。
    op.execute(
        "INSERT INTO agent_skill (id, title, description, body, tools, agents, origin, status,"
        " enabled, created_at, updated_at)"
        " SELECT id, title, when_to_use, body, NULL, NULL, 'override', 'active', enabled,"
        f" created_at, updated_at FROM agent_knowledge WHERE id IN ({_IN})"
    )
    op.execute(f"DELETE FROM agent_knowledge WHERE id IN ({_IN})")


def downgrade() -> None:
    # 只能还原 9 条的覆盖行；用户技能与生成的草稿随表删除，不可还原。
    op.execute(
        "INSERT OR REPLACE INTO agent_knowledge (id, kind, title, when_to_use, body, enabled,"
        " created_at, updated_at)"
        " SELECT id, NULL, title, substr(description, 1, 120), body, enabled, created_at, updated_at"
        f" FROM agent_skill WHERE origin = 'override' AND id IN ({_IN})"
    )
    op.drop_table("agent_skill")
