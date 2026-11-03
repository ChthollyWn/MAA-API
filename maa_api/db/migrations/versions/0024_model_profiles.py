"""M67：模型层两表。新表 ``model_profile`` 与 ``cost_ledger``（v3 docs/03 §4.1、§4.2）。

两张表都是**纯新增**，不碰任何既有表，也**不回填**任何数据：

- ``model_profile`` 是模型档案的持久化；``name`` 唯一（``default`` 档案不入表，
  由 ``llm.*`` 在进程内合成）。主键沿用对外资源的 36 字符 UUID 字符串。
- ``cost_ledger`` 是**一次模型调用一行**的高频追加表：``INTEGER PRIMARY KEY
  AUTOINCREMENT``（缺了 ``sqlite_autoincrement`` 关键字，按保留策略删到尾部后
  id 会复用）。两个关联列 ``run_id`` / ``session_id`` 用 ``ON DELETE SET NULL``：
  运行与会话可以删，成本账本仍要留着给预算与历史汇总。
- 两条索引：``ix_cost_ledger_created_at``（按时间清理 / 区间汇总）与
  ``ix_cost_ledger_agent_name_created_at``（按代理分组汇总）。

downgrade 只删两条索引与两张表：两张表都不是既有 schema 的一部分，
``agent_run`` 等既有数据不受影响。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_model_profiles"
down_revision: Union[str, Sequence[str], None] = "0023_agent_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_profile",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=48), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=24), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("supports_vision", sa.Boolean(), nullable=True),
        sa.Column("supports_tools", sa.Boolean(), nullable=False),
        sa.Column("supports_json_schema", sa.Boolean(), nullable=True),
        sa.Column("dynamic_context_position", sa.String(length=16), nullable=False),
        sa.Column("price_input", sa.Float(), nullable=True),
        sa.Column("price_cached_input", sa.Float(), nullable=True),
        sa.Column("price_output", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("local_model_path", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_model_profile"),
        sa.UniqueConstraint("name", name="uq_model_profile_name"),
    )
    op.create_table(
        "cost_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("agent_name", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("model_profile", sa.String(length=48), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_run.id"],
            name="fk_cost_ledger_run_id_agent_run",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_cost_ledger_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cost_ledger"),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_cost_ledger_created_at",
        "cost_ledger",
        ["created_at"],
    )
    op.create_index(
        "ix_cost_ledger_agent_name_created_at",
        "cost_ledger",
        ["agent_name", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cost_ledger_agent_name_created_at", table_name="cost_ledger"
    )
    op.drop_index("ix_cost_ledger_created_at", table_name="cost_ledger")
    op.drop_table("cost_ledger")
    op.drop_table("model_profile")
