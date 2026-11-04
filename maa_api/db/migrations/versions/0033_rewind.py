"""M80：会话回溯与分叉（v3 specs/m80 §4.1、v3 docs/03 §10.3）。

- ``agent_session`` 加 ``forked_from_session_id``（自引用，``SET NULL``）与
  ``forked_from_seq``；两列都可空，旧会话不受影响。
- ``agent_message`` 加 ``superseded_at``（可空 DATETIME）：回溯后被取代的时刻，
  非空的消息不进上下文，前端默认折叠。
- SQLite 不能用 ALTER 加约束，``agent_session`` 只能 batch 重建；离线 ``--sql``
  模式没有连接可反射，所以显式给 ``copy_from``（与 0026 同一写法）。
  ``superseded_at`` 不带约束、不在索引与触发器里，降级直接 ``drop_column``
  （SQLite 3.35 起原生支持）。
- 没有数据迁移：升级只加可空列，降级只删列，既有行保持原样。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_rewind"
down_revision: Union[str, Sequence[str], None] = "0032_memory"
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
    sa.Table("confirmation", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("ticket", metadata, sa.Column("id", sa.String(36), primary_key=True))
    return metadata


def _agent_session_table(*, with_rewind_columns: bool) -> sa.Table:
    """0032 时点（含 M69 的 ``ticket_id``）的 ``agent_session`` 形状。"""
    metadata = _metadata()
    columns = [
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("base_url", sa.String(255), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "atomic_grant_id",
            sa.String(36),
            sa.ForeignKey("confirmation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("atomic_grant_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.Column("incident_key", sa.String(160), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'chat'")),
        sa.Column(
            "parent_session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column(
            "ticket_id",
            sa.String(36),
            sa.ForeignKey("ticket.id", ondelete="SET NULL"),
            nullable=True,
        ),
    ]
    if with_rewind_columns:
        columns += [
            sa.Column(
                "forked_from_session_id",
                sa.String(36),
                sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("forked_from_seq", sa.Integer(), nullable=True),
        ]
    table = sa.Table("agent_session", metadata, *columns, sa.PrimaryKeyConstraint("id"))
    sa.Index("ix_agent_session_last_message_at", table.c.last_message_at)
    sa.Index("uq_agent_session_incident_key", table.c.incident_key, unique=True)
    sa.Index("ix_agent_session_kind_status", table.c.kind, table.c.status)
    return table


def upgrade() -> None:
    with op.batch_alter_table(
        "agent_session", copy_from=_agent_session_table(with_rewind_columns=False)
    ) as batch_op:
        batch_op.add_column(
            sa.Column("forked_from_session_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("forked_from_seq", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_session_forked_from_session_id_agent_session",
            "agent_session",
            ["forked_from_session_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.add_column(
        "agent_message", sa.Column("superseded_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_message", "superseded_at")

    with op.batch_alter_table(
        "agent_session", copy_from=_agent_session_table(with_rewind_columns=True)
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_agent_session_forked_from_session_id_agent_session", type_="foreignkey"
        )
        batch_op.drop_column("forked_from_seq")
        batch_op.drop_column("forked_from_session_id")
