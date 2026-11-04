"""M69：工单领域五表与三处外键列（v3 M69 规格 §4.1）。

- 五张新表：``ticket``（工单）、``mandate``（建单时写死的委托）、
  ``ticket_report``（Worker 汇报）、``thread_subscription``（话题订阅）、
  ``agent_inbox``（收件箱）。
- 三张已有表加可空外键列：``agent_session.ticket_id`` / ``agent_run.ticket_id``
  → ``ticket.id``，``agent_message.inbox_id`` → ``agent_inbox.id``，三处都是
  ``ON DELETE SET NULL``：工单或收件箱被清理时，历史会话与消息仍留着，只是不再
  关联。
- SQLite 不能用 ALTER 加约束，三张表都必须 batch 重建；离线 ``--sql`` 模式没有
  连接可反射，所以三张表都显式给 ``copy_from``（与 0023 同一写法）。
  ``agent_message`` 是 AUTOINCREMENT 表，``copy_from`` 必须带
  ``sqlite_autoincrement=True``，否则重建后 DDL 丢掉关键字。
- 工单结束不是删除：``mandate`` / ``ticket_report`` / ``thread_subscription``
  随 ticket 级联删除；``ticket`` 的两个会话列与 ``agent_inbox.consumed_run_id``
  用 SET NULL。

downgrade 只删结构、不回填数据：先用 batch 删掉三处外键与列，再按
``agent_inbox`` → ``thread_subscription`` → ``ticket_report`` → ``mandate`` →
``ticket`` 的顺序删索引与表。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_tickets"
down_revision: Union[str, Sequence[str], None] = "0025_trace_spans"
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
    sa.Table("ticket", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("agent_inbox", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    return metadata


def _agent_session_table(*, with_ticket: bool = False) -> sa.Table:
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
    ]
    if with_ticket:
        columns.append(
            sa.Column(
                "ticket_id",
                sa.String(36),
                sa.ForeignKey("ticket.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    table = sa.Table("agent_session", metadata, *columns, sa.PrimaryKeyConstraint("id"))
    sa.Index("ix_agent_session_last_message_at", table.c.last_message_at)
    sa.Index("uq_agent_session_incident_key", table.c.incident_key, unique=True)
    sa.Index("ix_agent_session_kind_status", table.c.kind, table.c.status)
    return table


def _agent_run_table(*, with_ticket: bool = False) -> sa.Table:
    metadata = _metadata()
    sa.Table("agent_session", metadata, sa.Column("id", sa.String(36), primary_key=True))
    columns = [
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "parent_run_id",
            sa.String(36),
            sa.ForeignKey("agent_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("root_run_id", sa.String(36), nullable=False),
        sa.Column("agent_name", sa.String(32), nullable=False),
        sa.Column("spec_version", sa.String(16), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger_seq", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("model_profile", sa.String(48), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(8), nullable=True),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("boot_id", sa.String(36), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
    ]
    if with_ticket:
        columns.append(
            sa.Column(
                "ticket_id",
                sa.String(36),
                sa.ForeignKey("ticket.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    table = sa.Table("agent_run", metadata, *columns, sa.PrimaryKeyConstraint("id"))
    sa.Index(
        "uq_agent_run_active_session",
        table.c.session_id,
        unique=True,
        sqlite_where=sa.text(ACTIVE_RUN_PREDICATE),
    )
    sa.Index("ix_agent_run_session_id_started_at", table.c.session_id, table.c.started_at)
    sa.Index("ix_agent_run_root_run_id", table.c.root_run_id)
    return table


def _agent_message_table(*, with_inbox_id: bool = False) -> sa.Table:
    metadata = _metadata()
    sa.Table("agent_session", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("agent_run", metadata, sa.Column("id", sa.String(36), primary_key=True))
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
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
    ]
    if with_inbox_id:
        columns.append(
            sa.Column(
                "inbox_id",
                sa.Integer(),
                sa.ForeignKey("agent_inbox.id", ondelete="SET NULL"),
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
    sa.Index("ix_agent_message_run_id", table.c.run_id)
    return table


def upgrade() -> None:
    op.create_table(
        "ticket",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("orchestrator_session_id", sa.String(length=36), nullable=True),
        sa.Column("worker_session_id", sa.String(length=36), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("last_report_status", sa.String(length=16), nullable=True),
        sa.Column("human_action", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["orchestrator_session_id"],
            ["agent_session.id"],
            name="fk_ticket_orchestrator_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["worker_session_id"],
            ["agent_session.id"],
            name="fk_ticket_worker_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ticket"),
    )
    op.create_index("ix_ticket_status_updated_at", "ticket", ["status", "updated_at"])
    op.create_index("ix_ticket_kind_created_at", "ticket", ["kind", "created_at"])

    op.create_table(
        "mandate",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("strength", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("sender", sa.String(length=128), nullable=True),
        sa.Column("rule_id", sa.String(length=48), nullable=True),
        sa.Column("risk_ceiling", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_mandate_ticket_id_ticket",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mandate"),
        sa.UniqueConstraint("ticket_id", name="uq_mandate_ticket_id"),
    )

    op.create_table(
        "ticket_report",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("human_action", sa.JSON(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("forwarded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_ticket_report_ticket_id_ticket",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_run.id"],
            name="fk_ticket_report_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ticket_report"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_ticket_report_ticket_id_created_at",
        "ticket_report",
        ["ticket_id", "created_at"],
    )

    op.create_table(
        "thread_subscription",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=False),
        sa.Column("thread_key", sa.String(length=160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_thread_subscription_ticket_id_ticket",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_thread_subscription"),
        sa.UniqueConstraint(
            "ticket_id",
            "thread_key",
            name="uq_thread_subscription_ticket_id_thread_key",
        ),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_thread_subscription_thread_key_active",
        "thread_subscription",
        ["thread_key", "active"],
    )

    op.create_table(
        "agent_inbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("thread_key", sa.String(length=160), nullable=True),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_run_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_agent_inbox_session_id_agent_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["consumed_run_id"],
            ["agent_run.id"],
            name="fk_agent_inbox_consumed_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_inbox"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_agent_inbox_session_id_status_id",
        "agent_inbox",
        ["session_id", "status", "id"],
    )

    with op.batch_alter_table(
        "agent_session", copy_from=_agent_session_table(with_ticket=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("ticket_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_session_ticket_id_ticket",
            "ticket",
            ["ticket_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table(
        "agent_run", copy_from=_agent_run_table(with_ticket=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("ticket_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_run_ticket_id_ticket",
            "ticket",
            ["ticket_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table(
        "agent_message", copy_from=_agent_message_table(with_inbox_id=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("inbox_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_message_inbox_id_agent_inbox",
            "agent_inbox",
            ["inbox_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "agent_message", copy_from=_agent_message_table(with_inbox_id=True)
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_agent_message_inbox_id_agent_inbox", type_="foreignkey"
        )
        batch_op.drop_column("inbox_id")

    with op.batch_alter_table(
        "agent_run", copy_from=_agent_run_table(with_ticket=True)
    ) as batch_op:
        batch_op.drop_constraint("fk_agent_run_ticket_id_ticket", type_="foreignkey")
        batch_op.drop_column("ticket_id")

    with op.batch_alter_table(
        "agent_session", copy_from=_agent_session_table(with_ticket=True)
    ) as batch_op:
        batch_op.drop_constraint("fk_agent_session_ticket_id_ticket", type_="foreignkey")
        batch_op.drop_column("ticket_id")

    op.drop_index("ix_agent_inbox_session_id_status_id", table_name="agent_inbox")
    op.drop_table("agent_inbox")

    op.drop_index(
        "ix_thread_subscription_thread_key_active", table_name="thread_subscription"
    )
    op.drop_table("thread_subscription")

    op.drop_index(
        "ix_ticket_report_ticket_id_created_at", table_name="ticket_report"
    )
    op.drop_table("ticket_report")

    op.drop_table("mandate")

    op.drop_index("ix_ticket_kind_created_at", table_name="ticket")
    op.drop_index("ix_ticket_status_updated_at", table_name="ticket")
    op.drop_table("ticket")
