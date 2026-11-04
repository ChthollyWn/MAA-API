"""M71：设备租约表与来源/归属列（v3 M71 规格 §4.1）。

- 新表 ``device_lease``：主键 ``device_id`` 保证设备容量 1，释放即删除行；
  ``lease_id`` 唯一，``ticket_id`` / ``run_id`` 指向工单与根运行，都是
  ``ON DELETE SET NULL``（租约不该让工单或运行删不掉）。
- 三张已有表加可空列：``confirmation`` / ``agent_question`` 各加
  ``origin_run_id`` / ``origin_ticket_id`` / ``origin_agent``（发起来源），
  ``agent_audit`` 加 ``run_id`` / ``ticket_id`` / ``agent_name``（调用归属）与
  ``ix_agent_audit_ticket_id_created_at`` 索引。六个新外键都是 ``SET NULL``。
- SQLite 不能用 ALTER 加约束，三张表都必须 batch 重建；离线 ``--sql`` 模式没有
  连接可反射，所以三张表都显式给 ``copy_from``（与 0023、0026 同一写法），
  逐列抄改动前的定义：主键名、全部外键与 ondelete、索引、``agent_audit`` 的
  ``sqlite_autoincrement``。``agent_audit`` 的物理列序是 0001 建表后再由 0008 /
  0010 追加 ``request_id`` / ``scopes``，因此这两列在 helper 里排在最后。
- downgrade 逆序：三个 batch 删索引、外键与列，最后删 ``device_lease``。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_device_lease"
down_revision: Union[str, Sequence[str], None] = "0026_tickets"
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


def _copy_metadata(*targets: str) -> sa.MetaData:
    """copy_from 用的元数据；外键目标表只需要主键列。"""
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    for target in targets:
        if target in ("ticket", "agent_run", "agent_session", "confirmation"):
            sa.Table(target, metadata, sa.Column("id", sa.String(36), primary_key=True))
        elif target == "agent_audit":
            sa.Table("agent_audit", metadata, sa.Column("id", sa.Integer(), primary_key=True))
        else:  # pragma: no cover - 迁移里只有上面这些目标表
            raise ValueError(target)
    return metadata


def _confirmation_table(*, with_origin: bool) -> sa.Table:
    metadata = _copy_metadata("agent_audit", "agent_run", "ticket")
    columns = [
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.String(16), nullable=False),
        sa.Column(
            "audit_id",
            sa.Integer(),
            sa.ForeignKey("agent_audit.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_by", sa.String(32), nullable=True),
        sa.Column("resolved_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    ]
    if with_origin:
        columns += [
            sa.Column(
                "origin_run_id",
                sa.String(36),
                sa.ForeignKey("agent_run.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "origin_ticket_id",
                sa.String(36),
                sa.ForeignKey("ticket.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("origin_agent", sa.String(32), nullable=True),
        ]
    table = sa.Table("confirmation", metadata, *columns, sa.PrimaryKeyConstraint("id"))
    sa.Index("ix_confirmation_status_expires_at", table.c.status, table.c.expires_at)
    return table


def _agent_question_table(*, with_origin: bool) -> sa.Table:
    metadata = _copy_metadata("agent_session", "agent_audit", "agent_run", "ticket")
    columns = [
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("multiple", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("selection", sa.JSON(), nullable=True),
        sa.Column("answered_by", sa.String(32), nullable=True),
        sa.Column(
            "audit_id",
            sa.Integer(),
            sa.ForeignKey("agent_audit.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
    ]
    if with_origin:
        columns += [
            sa.Column(
                "origin_run_id",
                sa.String(36),
                sa.ForeignKey("agent_run.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "origin_ticket_id",
                sa.String(36),
                sa.ForeignKey("ticket.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("origin_agent", sa.String(32), nullable=True),
        ]
    table = sa.Table(
        "agent_question", metadata, *columns, sa.PrimaryKeyConstraint("id")
    )
    sa.Index("ix_agent_question_status_expires_at", table.c.status, table.c.expires_at)
    sa.Index(
        "ix_agent_question_session_status_created_at",
        table.c.session_id,
        table.c.status,
        table.c.created_at,
    )
    return table


def _agent_audit_table(*, with_origin: bool) -> sa.Table:
    metadata = _copy_metadata(
        "confirmation", "agent_session", "agent_run", "ticket"
    )
    # 0001 建表后由 0008 / 0010 追加 request_id / scopes，物理列序在最后。
    columns = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("caller", sa.String(16), nullable=False),
        sa.Column("caller_detail", sa.String(128), nullable=True),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("arguments", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(48), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column(
            "confirmation_id",
            sa.String(36),
            sa.ForeignKey("confirmation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "authorized_by_id",
            sa.String(36),
            sa.ForeignKey("confirmation.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("agent_session.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("scopes", sa.JSON(none_as_null=True), nullable=True),
    ]
    if with_origin:
        columns += [
            sa.Column(
                "run_id",
                sa.String(36),
                sa.ForeignKey("agent_run.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "ticket_id",
                sa.String(36),
                sa.ForeignKey("ticket.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("agent_name", sa.String(32), nullable=True),
        ]
    table = sa.Table(
        "agent_audit",
        metadata,
        *columns,
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )
    sa.Index("ix_agent_audit_caller_created_at", table.c.caller, table.c.created_at)
    sa.Index("ix_agent_audit_tool_name_created_at", table.c.tool_name, table.c.created_at)
    if with_origin:
        sa.Index(
            "ix_agent_audit_ticket_id_created_at", table.c.ticket_id, table.c.created_at
        )
    return table


def upgrade() -> None:
    op.create_table(
        "device_lease",
        sa.Column("device_id", sa.String(length=32), nullable=False),
        sa.Column("lease_id", sa.String(length=36), nullable=False),
        sa.Column("ticket_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("holder_agent", sa.String(length=32), nullable=False),
        sa.Column("purpose", sa.String(length=120), nullable=False),
        sa.Column("explicit", sa.Boolean(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.Column("boot_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["ticket.id"],
            name="fk_device_lease_ticket_id_ticket",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_run.id"],
            name="fk_device_lease_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("device_id", name="pk_device_lease"),
        sa.UniqueConstraint("lease_id", name="uq_device_lease_lease_id"),
    )

    with op.batch_alter_table(
        "confirmation", copy_from=_confirmation_table(with_origin=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("origin_run_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("origin_ticket_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("origin_agent", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_confirmation_origin_run_id_agent_run",
            "agent_run",
            ["origin_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_confirmation_origin_ticket_id_ticket",
            "ticket",
            ["origin_ticket_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table(
        "agent_question", copy_from=_agent_question_table(with_origin=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("origin_run_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("origin_ticket_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(sa.Column("origin_agent", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_question_origin_run_id_agent_run",
            "agent_run",
            ["origin_run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_agent_question_origin_ticket_id_ticket",
            "ticket",
            ["origin_ticket_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table(
        "agent_audit", copy_from=_agent_audit_table(with_origin=False)
    ) as batch_op:
        batch_op.add_column(sa.Column("run_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("ticket_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("agent_name", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_agent_audit_run_id_agent_run",
            "agent_run",
            ["run_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_agent_audit_ticket_id_ticket",
            "ticket",
            ["ticket_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            "ix_agent_audit_ticket_id_created_at", ["ticket_id", "created_at"]
        )


def downgrade() -> None:
    with op.batch_alter_table(
        "agent_audit", copy_from=_agent_audit_table(with_origin=True)
    ) as batch_op:
        batch_op.drop_index("ix_agent_audit_ticket_id_created_at")
        batch_op.drop_constraint("fk_agent_audit_run_id_agent_run", type_="foreignkey")
        batch_op.drop_constraint("fk_agent_audit_ticket_id_ticket", type_="foreignkey")
        batch_op.drop_column("run_id")
        batch_op.drop_column("ticket_id")
        batch_op.drop_column("agent_name")

    with op.batch_alter_table(
        "agent_question", copy_from=_agent_question_table(with_origin=True)
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_agent_question_origin_run_id_agent_run", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_agent_question_origin_ticket_id_ticket", type_="foreignkey"
        )
        batch_op.drop_column("origin_run_id")
        batch_op.drop_column("origin_ticket_id")
        batch_op.drop_column("origin_agent")

    with op.batch_alter_table(
        "confirmation", copy_from=_confirmation_table(with_origin=True)
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_confirmation_origin_run_id_agent_run", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_confirmation_origin_ticket_id_ticket", type_="foreignkey"
        )
        batch_op.drop_column("origin_run_id")
        batch_op.drop_column("origin_ticket_id")
        batch_op.drop_column("origin_agent")

    op.drop_table("device_lease")
