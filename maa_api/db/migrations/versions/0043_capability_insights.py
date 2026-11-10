"""M99：能力效果看板三表 ``eval_run`` / ``eval_case_result`` / ``run_feedback``（spec §4.1）。

- 纯新增，不回填：三张表逐列照 spec §4.1 的模型展开，列宽、可空性与默认值
  （Python 侧默认值只在模型里，DDL 不加 server_default）保持一致。
- ``eval_run``：主键是评测结果 id（``<日期>-<时间>-<套件>-<模式>-<短哈希>``，
  最长 64），不自增；``started_at`` / ``finished_at`` 必填，``currency`` 与三个
  快照 JSON 列可空。两条索引都服务「按完成时间倒序列评测列表」。
- ``eval_case_result``：``id`` 是 ``INTEGER PRIMARY KEY AUTOINCREMENT``（高频追加，
  删到尾部后不复用）；``eval_run_id`` 外键到 ``eval_run.id`` 且
  ``ON DELETE CASCADE``（评测删了，用例没有独立存在的意义）。
  ``(eval_run_id, scenario, attempt, variant)`` 唯一：重复发布同一结果不会插重行。
  ``tags`` 是场景 tags 快照，``metrics`` / ``tags`` / ``graders`` 都是非空 JSON。
- ``run_feedback``：``id`` 自增；``run_id`` 外键到 ``agent_run.id`` 且
  ``ON DELETE CASCADE``；``run_id`` 唯一保证一次运行至多一条评价（重复提交在仓储
  里覆盖）。``created_at`` 之外还有 ``updated_at``，覆盖时保留前者。
- 索引 5 条：``ix_eval_run_finished_at``、``ix_eval_run_suite_finished_at``、
  ``ix_eval_case_result_eval_run_id``、``ix_eval_case_result_scenario``、
  ``ix_run_feedback_created_at``。降级反序删索引与表。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043_capability_insights"
down_revision: Union[str, Sequence[str], None] = "0042_demonstration"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eval_run",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("suite", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("error", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("pass_rate", sa.Float(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("git", sa.JSON(), nullable=True),
        sa.Column("model", sa.JSON(), nullable=True),
        sa.Column("filters", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_eval_run"),
    )
    op.create_table(
        "eval_case_result",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("eval_run_id", sa.String(length=64), nullable=False),
        sa.Column("scenario", sa.String(length=96), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("suite", sa.String(length=16), nullable=False),
        sa.Column("agent", sa.String(length=32), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("variant", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("graders", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["eval_run.id"],
            name="fk_eval_case_result_eval_run_id_eval_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_eval_case_result"),
        sa.UniqueConstraint(
            "eval_run_id",
            "scenario",
            "attempt",
            "variant",
            name="uq_eval_case_result_run_scenario_attempt_variant",
        ),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "run_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_run.id"],
            name="fk_run_feedback_run_id_agent_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_run_feedback"),
        sa.UniqueConstraint("run_id", name="uq_run_feedback_run_id"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_eval_run_finished_at", "eval_run", ["finished_at"])
    op.create_index(
        "ix_eval_run_suite_finished_at", "eval_run", ["suite", "finished_at"]
    )
    op.create_index(
        "ix_eval_case_result_eval_run_id", "eval_case_result", ["eval_run_id"]
    )
    op.create_index("ix_eval_case_result_scenario", "eval_case_result", ["scenario"])
    op.create_index("ix_run_feedback_created_at", "run_feedback", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_run_feedback_created_at", table_name="run_feedback")
    op.drop_index("ix_eval_case_result_scenario", table_name="eval_case_result")
    op.drop_index(
        "ix_eval_case_result_eval_run_id", table_name="eval_case_result"
    )
    op.drop_index("ix_eval_run_suite_finished_at", table_name="eval_run")
    op.drop_index("ix_eval_run_finished_at", table_name="eval_run")
    op.drop_table("run_feedback")
    op.drop_table("eval_case_result")
    op.drop_table("eval_run")
