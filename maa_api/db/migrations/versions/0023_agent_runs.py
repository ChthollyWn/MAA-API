"""M66：运行树。新表 agent_run；agent_session 加 kind / parent_session_id / archived_at；
agent_message 加 run_id。

- agent_session 与 agent_message 都要加带外键的列。SQLite 不能用 ALTER 加约束
  （Alembic 会报 NotImplementedError，而且列已经加上了），只能 batch 重建；离线
  ``--sql`` 模式没有连接可反射，所以两张表都显式给 ``copy_from``，结构与 0022 一致。
- agent_message 是 AUTOINCREMENT 表：copy_from 必须带 ``sqlite_autoincrement=True``，
  否则重建后 DDL 丢掉关键字；重建还会把 sqlite_sequence 降到现存的最大 id，这里先存后还，
  保证尾部删过的 id 不被复用。
- 数据迁移只有一条：incident_key 非空的会话写 kind = 'incident'。
- downgrade 完全可逆：删列与删表；kind 可由 incident_key 推回。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_agent_runs"
down_revision: Union[str, Sequence[str], None] = "0022_skland_only_operators"
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
ACTIVE_RUN_PREDICATE = "status IN ('running', 'waiting_human')"


def _metadata() -> sa.MetaData:
    """copy_from 用的元数据；外键目标表只需要主键列。"""
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    sa.Table("confirmation", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("agent_audit", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table("agent_run", metadata, sa.Column("id", sa.String(36), primary_key=True))
    return metadata


def _agent_session_table(*, with_run_columns: bool) -> sa.Table:
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
    ]
    if with_run_columns:
        columns += [
            sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'chat'")),
            sa.Column(
                "parent_session_id",
                sa.String(36),
                sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("archived_at", sa.DateTime(), nullable=True),
        ]
    table = sa.Table("agent_session", metadata, *columns, sa.PrimaryKeyConstraint("id"))
    sa.Index("ix_agent_session_last_message_at", table.c.last_message_at)
    sa.Index("uq_agent_session_incident_key", table.c.incident_key, unique=True)
    if with_run_columns:
        sa.Index("ix_agent_session_kind_status", table.c.kind, table.c.status)
    return table


def _agent_message_table(*, with_run_id: bool) -> sa.Table:
    metadata = _metadata()
    sa.Table("agent_session", metadata, sa.Column("id", sa.String(36), primary_key=True))
    columns = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("tool_call_id", sa.String(64), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column(
            "audit_id",
            sa.Integer(),
            sa.ForeignKey("agent_audit.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.String(24), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
    ]
    if with_run_id:
        columns.append(
            sa.Column(
                "run_id",
                sa.String(36),
                sa.ForeignKey("agent_run.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    table = sa.Table(
        "agent_message",
        metadata,
        *columns,
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "seq", name="uq_agent_message_session_seq"),
        sqlite_autoincrement=True,
    )
    if with_run_id:
        sa.Index("ix_agent_message_run_id", table.c.run_id)
    return table


def _stash_message_sequence() -> None:
    op.execute(
        "CREATE TABLE _m66_agent_message_seq AS "
        "SELECT seq FROM sqlite_sequence WHERE name = 'agent_message'"
    )


def _restore_message_sequence() -> None:
    op.execute(
        "UPDATE sqlite_sequence SET seq = (SELECT seq FROM _m66_agent_message_seq) "
        "WHERE name = 'agent_message' AND seq < (SELECT seq FROM _m66_agent_message_seq)"
    )
    op.execute("DROP TABLE _m66_agent_message_seq")


def upgrade() -> None:
    op.create_table(
        "agent_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("root_run_id", sa.String(length=36), nullable=False),
        sa.Column("agent_name", sa.String(length=32), nullable=False),
        sa.Column("spec_version", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trigger_seq", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("model_profile", sa.String(length=48), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("boot_id", sa.String(length=36), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["agent_session.id"],
            name="fk_agent_run_session_id_agent_session", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["agent_run.id"],
            name="fk_agent_run_parent_run_id_agent_run", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run"),
    )
    op.create_index(
        "uq_agent_run_active_session", "agent_run", ["session_id"], unique=True,
        sqlite_where=sa.text(ACTIVE_RUN_PREDICATE),
    )
    op.create_index("ix_agent_run_session_id_started_at", "agent_run", ["session_id", "started_at"])
    op.create_index("ix_agent_run_root_run_id", "agent_run", ["root_run_id"])

    with op.batch_alter_table(
        "agent_session", copy_from=_agent_session_table(with_run_columns=False)
    ) as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default=sa.text("'chat'"))
        )
        batch_op.add_column(sa.Column("parent_session_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_session_parent_session_id_agent_session",
            "agent_session", ["parent_session_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_index("ix_agent_session_kind_status", ["kind", "status"])
    op.execute("UPDATE agent_session SET kind = 'incident' WHERE incident_key IS NOT NULL")

    _stash_message_sequence()
    with op.batch_alter_table(
        "agent_message", copy_from=_agent_message_table(with_run_id=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("run_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_message_run_id_agent_run",
            "agent_run", ["run_id"], ["id"], ondelete="SET NULL",
        )
        batch_op.create_index("ix_agent_message_run_id", ["run_id"])
    _restore_message_sequence()


def downgrade() -> None:
    _stash_message_sequence()
    with op.batch_alter_table(
        "agent_message", copy_from=_agent_message_table(with_run_id=True)
    ) as batch_op:
        batch_op.drop_index("ix_agent_message_run_id")
        batch_op.drop_constraint("fk_agent_message_run_id_agent_run", type_="foreignkey")
        batch_op.drop_column("run_id")
    _restore_message_sequence()
    with op.batch_alter_table(
        "agent_session", copy_from=_agent_session_table(with_run_columns=True)
    ) as batch_op:
        batch_op.drop_index("ix_agent_session_kind_status")
        batch_op.drop_constraint(
            "fk_agent_session_parent_session_id_agent_session", type_="foreignkey"
        )
        batch_op.drop_column("archived_at")
        batch_op.drop_column("parent_session_id")
        batch_op.drop_column("kind")
    op.drop_index("ix_agent_run_root_run_id", table_name="agent_run")
    op.drop_index("ix_agent_run_session_id_started_at", table_name="agent_run")
    op.drop_index("uq_agent_run_active_session", table_name="agent_run")
    op.drop_table("agent_run")
