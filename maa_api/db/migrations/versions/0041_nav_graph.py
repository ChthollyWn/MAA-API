"""M86：界面导航图两表 ``nav_node`` / ``nav_edge``（v3 specs/m86 §4.1、docs/v3/03 §12）。

- 纯新增，不回填：两张表逐列照 spec §4.1，不新增列或索引。
- ``nav_node``：``id`` 是短 slug 主键（最长 48，``stage:<代号>`` 也在其中）；
  ``exemplars`` / ``recognizer`` 是带 server_default 的 JSON 列。
- ``nav_edge``：36 字符 UUID 主键；``from_node`` / ``to_node`` 外键到 ``nav_node.id``
  且 ``ON DELETE CASCADE``（节点删了，挂在它两端的跳转没有独立存在的意义）；
  唯一索引 ``uq_nav_edge_from_node_to_node_action_hash`` 保证同一对界面的同一动作
  只落一条跳转；``action`` 是 JSON、``cost_ms`` / ``last_used_at`` 可空。
- 迁移不写种子数据：种子由 ``NavGraphService.ensure_seed()`` 在 ``nav_node`` 为空时
  写入（spec §4.5.6）。降级丢掉学到的图，属于运行时数据，可接受。
- 降级反序：先删唯一索引，再 ``nav_edge``，最后 ``nav_node``。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_nav_graph"
down_revision: Union[str, Sequence[str], None] = "0040_question_expiry_historical"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nav_node",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("exemplars", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recognizer", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_nav_node"),
    )
    op.create_table(
        "nav_edge",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("from_node", sa.String(length=48), nullable=False),
        sa.Column("to_node", sa.String(length=48), nullable=False),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("cost_ms", sa.Integer(), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("fail_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["from_node"],
            ["nav_node.id"],
            name="fk_nav_edge_from_node_nav_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_node"],
            ["nav_node.id"],
            name="fk_nav_edge_to_node_nav_node",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nav_edge"),
    )
    op.create_index(
        "uq_nav_edge_from_node_to_node_action_hash",
        "nav_edge",
        ["from_node", "to_node", "action_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_nav_edge_from_node_to_node_action_hash", table_name="nav_edge")
    op.drop_table("nav_edge")
    op.drop_table("nav_node")
