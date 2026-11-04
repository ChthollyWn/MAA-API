"""M68：运行追踪表 ``trace_span``（v3 docs/03 §5.1）。

纯新增表，不碰既有表、不回填数据：span 在结束时整行写入，历史运行没有追踪记录
属于预期（打开追踪之前的运行就是没有）。

- 主键是 ``INTEGER PRIMARY KEY AUTOINCREMENT``：高频追加表，缺了
  ``sqlite_autoincrement`` 关键字，按保留期删到尾部后 id 会复用（与
  ``cost_ledger`` 同一个理由，docs/04 §3.1）。
- ``run_id`` 用 ``ON DELETE CASCADE``：运行删了，它名下的 span 没有独立存在的
  意义；``trace_id`` / ``span_id`` 唯一，重复写入同一 span 会被数据库拒绝。
- 两条索引：``ix_trace_span_run_id``（按运行取整条 trace）与
  ``ix_trace_span_started_at``（按保留期清理）。

downgrade 只删两条索引与这张表：``agent_run`` 等既有数据不受影响。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_trace_spans"
down_revision: Union[str, Sequence[str], None] = "0024_model_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "trace_span",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=36), nullable=False),
        sa.Column("span_id", sa.String(length=16), nullable=False),
        sa.Column("parent_span_id", sa.String(length=16), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_run.id"],
            name="fk_trace_span_run_id_agent_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_trace_span"),
        sa.UniqueConstraint(
            "trace_id", "span_id", name="uq_trace_span_trace_id_span_id"
        ),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_trace_span_run_id", "trace_span", ["run_id"])
    op.create_index("ix_trace_span_started_at", "trace_span", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_trace_span_started_at", table_name="trace_span")
    op.drop_index("ix_trace_span_run_id", table_name="trace_span")
    op.drop_table("trace_span")
