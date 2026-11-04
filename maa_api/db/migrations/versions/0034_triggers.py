"""M81：事件源三表与 ``ticket.source_trigger_id``（v3 specs/m81 §4.1、docs/v3/03 §11）。

- 三张新表：``agent_trigger``（进入系统的事件）、``trigger_rule``（内置规则的覆盖层
  与用户规则）、``trigger_observation``（observe 档的观察记录）。
- 已有表加一列：``ticket.source_trigger_id`` → ``agent_trigger.id``，``ON DELETE SET
  NULL``。它与 ``agent_trigger.ticket_id`` → ``ticket.id`` 构成循环外键，处理方式与
  M69 的 ``agent_session.ticket_id`` ↔ ``ticket.worker_session_id`` 一致：模型里双向
  声明，迁移里先建 ``agent_trigger``，再用 batch 给 ``ticket`` 加列与外键。
- SQLite 不能用 ALTER 加约束，``ticket`` 只能 batch 重建；离线 ``--sql`` 模式没有
  连接可反射，所以显式给 ``copy_from``（与 0021、0026、0033 同一写法）。``copy_from``
  必须与 batch 开始时的表一致：upgrade 时没有 ``source_trigger_id``，downgrade 时
  列与外键都还在。``ticket`` 原有的两条索引也要声明，否则 batch 重建后无处可删。
- 降级会丢掉全部事件、规则覆盖与观察记录，这是可接受的：它们都能从系统重新产生，
  用户规则需要重新建；``ticket`` 自己的数据不丢。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_triggers"
down_revision: Union[str, Sequence[str], None] = "0033_rewind"
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
    sa.Table("agent_session", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table("agent_trigger", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    return metadata


def _ticket_table(*, with_source_trigger: bool) -> sa.Table:
    """``ticket`` 在本迁移视角的定义，只给 batch 的 copy_from 用（照 0021_depot_sources）。

    列、外键、索引逐项照 ``db/models.py`` 的 Ticket（M69，03 §6.1）抄；
    ``with_source_trigger=True`` 时多 ``source_trigger_id`` 与它的外键。
    """
    metadata = _metadata()
    columns = [
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "orchestrator_session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "worker_session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("last_report_status", sa.String(16), nullable=True),
        sa.Column("human_action", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
    ]
    if with_source_trigger:
        columns.append(
            sa.Column(
                "source_trigger_id",
                sa.Integer(),
                sa.ForeignKey("agent_trigger.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    table = sa.Table("ticket", metadata, *columns, sa.PrimaryKeyConstraint("id"))
    sa.Index("ix_ticket_status_updated_at", table.c.status, table.c.updated_at)
    sa.Index("ix_ticket_kind_created_at", table.c.kind, table.c.created_at)
    return table


def upgrade() -> None:
    # 三张表的列逐个照模型写：str(n)→sa.String(length=n)，int→sa.Integer()，
    # JSON→sa.JSON()，TEXT→sa.Text()，datetime→sa.DateTime()，bool→sa.Boolean()；
    # 可空性照模型；agent_trigger.payload 带 server_default=sa.text("'{}'")。
    op.create_table(
        "agent_trigger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("thread_key", sa.String(length=160), nullable=False),
        sa.Column("sender", sa.String(length=128), nullable=True),
        sa.Column(
            "payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("dedupe_key", sa.String(length=160), nullable=True),
        sa.Column("admission", sa.String(length=16), nullable=False),
        sa.Column("rule_id", sa.String(length=48), nullable=True),
        sa.Column("eval_status", sa.String(length=16), nullable=False),
        sa.Column("eval_reason", sa.Text(), nullable=True),
        sa.Column("eval_run_id", sa.String(length=36), nullable=True),
        sa.Column("ticket_id", sa.String(length=36), nullable=True),
        sa.Column("routed_to", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["agent_run.id"],
            name="fk_agent_trigger_eval_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_agent_trigger_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_trigger"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "uq_agent_trigger_dedupe_key",
        "agent_trigger",
        ["dedupe_key"],
        unique=True,
        sqlite_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_trigger_thread_key_created_at",
        "agent_trigger",
        ["thread_key", "created_at"],
    )
    op.create_index("ix_agent_trigger_created_at", "agent_trigger", ["created_at"])

    op.create_table(
        "trigger_rule",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=24), nullable=True),
        sa.Column("kind_pattern", sa.String(length=64), nullable=True),
        sa.Column("conditions", sa.JSON(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column("worker", sa.String(length=32), nullable=True),
        sa.Column("policy", sa.Text(), nullable=True),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=True),
        sa.Column("cron", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_trigger_rule"),
    )

    op.create_table(
        "trigger_observation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trigger_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=48), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["agent_trigger.id"],
            name="fk_trigger_observation_trigger_id_agent_trigger",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trigger_observation"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_trigger_observation_rule_id_created_at",
        "trigger_observation",
        ["rule_id", "created_at"],
    )

    with op.batch_alter_table(
        "ticket", copy_from=_ticket_table(with_source_trigger=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("source_trigger_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_ticket_source_trigger_id_agent_trigger",
            "agent_trigger",
            ["source_trigger_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "ticket", copy_from=_ticket_table(with_source_trigger=True)
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_ticket_source_trigger_id_agent_trigger", type_="foreignkey"
        )
        batch_op.drop_column("source_trigger_id")

    op.drop_index(
        "ix_trigger_observation_rule_id_created_at", table_name="trigger_observation"
    )
    op.drop_table("trigger_observation")
    op.drop_table("trigger_rule")
    for name in (
        "ix_agent_trigger_created_at",
        "ix_agent_trigger_thread_key_created_at",
        "uq_agent_trigger_dedupe_key",
    ):
        op.drop_index(name, table_name="agent_trigger")
    op.drop_table("agent_trigger")
