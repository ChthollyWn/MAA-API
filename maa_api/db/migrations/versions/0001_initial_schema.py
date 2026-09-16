"""0001 初始 schema：13 张业务表与 docs/04 §6 的全部索引。

Revision ID: 0001
Revises:
Create Date: 2026-09-16 19:21:32.063339

由 ``alembic revision --autogenerate`` 生成骨架后**逐条人工核对**（docs/04 §8.2
要求，M2-01 的 review 清单见 tests/fixtures/db_probe_findings.md §3/§6）：

1. 13 张表全在，列名/类型/可空性与 ``maa_api/db/models.py`` 一致；
2. 索引名与 docs/04 §6 一致；``uq_update_record_running_target`` 是带
   ``sqlite_where`` 的部分唯一索引（Alembic 1.20 本次保住了谓词，仍按纪律复核）；
3. ``log_entry`` / ``agent_message`` / ``agent_audit`` 三张高频追加表带
   ``sqlite_autoincrement=True``，DDL 里才有 ``AUTOINCREMENT``；
4. JSON 列的 ``server_default``：``task.params`` 为 ``'{}'``，
   其余 JSON 列无默认值；
5. 外键 ``ondelete``：``task``→``pipeline``、``agent_message``→``agent_session``
   是 ``CASCADE``，其余关联列是 ``SET NULL``；
6. 所有命名约束（pk/uq/ck/fk）显式写出 —— SQLite 删不掉匿名约束，将来 batch
   重建表时要按名字引用。

本迁移只建表、不重建表，所以不需要 ``batch_alter_table``，也不需要 ``copy_from``
（反射式 batch 重建会静默丢 ``AUTOINCREMENT``，那是后续迁移的纪律）。

``PRAGMA auto_vacuum=INCREMENTAL`` 不在这里：写在 ``upgrade()`` 首行会静默失效
（M2-01 实测读回 0），它归 ``migrations/env.py`` 的 ``run_migrations_online()``。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "pipeline",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("core_id", sa.String(length=32), nullable=False),
        sa.Column("core_epoch", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("task_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("schedule_id", sa.String(length=36), nullable=True),
        sa.Column("agent_session_id", sa.String(length=36), nullable=True),
        sa.Column("retry_of_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("notify_on_finish", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_session_id"],
            ["agent_session.id"],
            name="fk_pipeline_agent_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_id"],
            ["pipeline.id"],
            name="fk_pipeline_retry_of_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["schedule.id"],
            name="fk_pipeline_schedule_id_schedule",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pipeline"),
        sa.UniqueConstraint("idempotency_key", name="uq_pipeline_idempotency_key"),
    )
    op.create_index(
        "ix_pipeline_dequeue",
        "pipeline",
        ["core_id", "status", "priority", "created_at"],
    )
    op.create_index(
        "ix_pipeline_status_created_at", "pipeline", ["status", "created_at"]
    )
    op.create_index(
        "ix_pipeline_source_created_at", "pipeline", ["source", "created_at"]
    )
    op.create_index(
        "ix_pipeline_schedule_id_created_at",
        "pipeline",
        ["schedule_id", "created_at"],
    )

    op.create_table(
        "task",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("type_name", sa.String(length=32), nullable=False),
        sa.Column("task_name", sa.String(length=32), nullable=False),
        sa.Column(
            "params",
            sa.JSON(none_as_null=True),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("raw_params", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("max_retries", sa.Integer(), nullable=False),
        sa.Column("retry_delay", sa.Integer(), nullable=False),
        sa.Column("maa_task_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipeline.id"],
            name="fk_task_pipeline_id_pipeline",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_task"),
        sa.UniqueConstraint(
            "pipeline_id", "order_index", name="uq_task_pipeline_order"
        ),
        sa.UniqueConstraint(
            "pipeline_id", "maa_task_id", name="uq_task_pipeline_maa_task"
        ),
    )

    op.create_table(
        "log_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("core_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipeline.id"],
            name="fk_log_entry_pipeline_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_log_entry_task_id_task",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_log_entry"),
        # 高频追加表：DDL 必须落下 AUTOINCREMENT（docs/04 §3.1）
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_log_entry_source_created_at", "log_entry", ["source", "created_at"]
    )
    op.create_index("ix_log_entry_pipeline_id_id", "log_entry", ["pipeline_id", "id"])

    op.create_table(
        "screenshot",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipeline.id"],
            name="fk_screenshot_pipeline_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_screenshot_task_id_task",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_screenshot"),
    )
    op.create_index("ix_screenshot_created_at", "screenshot", ["created_at"])
    op.create_index(
        "ix_screenshot_pipeline_id_created_at",
        "screenshot",
        ["pipeline_id", "created_at"],
    )

    op.create_table(
        "schedule",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("cron", sa.String(length=64), nullable=False),
        sa.Column("timezone", sa.String(length=48), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("template", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("misfire_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("catch_up", sa.Boolean(), nullable=False),
        sa.Column("skip_if_running", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_pipeline_id", sa.String(length=36), nullable=True),
        sa.Column("last_result", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_pipeline_id"],
            ["pipeline.id"],
            name="fk_schedule_last_pipeline_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_schedule"),
        sa.UniqueConstraint("name", name="uq_schedule_name"),
    )
    op.create_index(
        "ix_schedule_enabled_next_run_at", "schedule", ["enabled", "next_run_at"]
    )

    op.create_table(
        "setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("updated_by", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_setting"),
    )

    op.create_table(
        "agent_session",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("atomic_grant_id", sa.String(length=36), nullable=True),
        sa.Column("atomic_grant_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["atomic_grant_id"],
            ["confirmation.id"],
            name="fk_agent_session_atomic_grant_id_confirmation",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_session"),
    )
    op.create_index(
        "ix_agent_session_last_message_at", "agent_session", ["last_message_at"]
    )

    op.create_table(
        "agent_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_calls", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("tool_call_id", sa.String(length=64), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=True),
        sa.Column("audit_id", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("finish_reason", sa.String(length=24), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["agent_audit.id"],
            name="fk_agent_message_audit_id_agent_audit",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_agent_message_session_id_agent_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_message"),
        sa.UniqueConstraint("session_id", "seq", name="uq_agent_message_session_seq"),
        # 高频追加表：DDL 必须落下 AUTOINCREMENT（docs/04 §3.1）
        sqlite_autoincrement=True,
    )

    op.create_table(
        "agent_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("caller", sa.String(length=16), nullable=False),
        sa.Column("caller_detail", sa.String(length=128), nullable=True),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("arguments", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("forced", sa.Boolean(), nullable=False),
        sa.Column("confirmation_id", sa.String(length=36), nullable=True),
        sa.Column("authorized_by_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["authorized_by_id"],
            ["confirmation.id"],
            name="fk_agent_audit_authorized_by_id_confirmation",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["confirmation_id"],
            ["confirmation.id"],
            name="fk_agent_audit_confirmation_id_confirmation",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_session.id"],
            name="fk_agent_audit_session_id_agent_session",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_audit"),
        # 高频追加表：DDL 必须落下 AUTOINCREMENT（docs/04 §3.1）
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_agent_audit_caller_created_at", "agent_audit", ["caller", "created_at"]
    )
    op.create_index(
        "ix_agent_audit_tool_name_created_at",
        "agent_audit",
        ["tool_name", "created_at"],
    )

    op.create_table(
        "confirmation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_by", sa.String(length=16), nullable=False),
        sa.Column("audit_id", sa.Integer(), nullable=True),
        sa.Column("resolved_by", sa.String(length=32), nullable=True),
        sa.Column("resolved_reason", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["agent_audit.id"],
            name="fk_confirmation_audit_id_agent_audit",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_confirmation"),
    )
    op.create_index(
        "ix_confirmation_status_expires_at", "confirmation", ["status", "expires_at"]
    )

    op.create_table(
        "update_record",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target", sa.String(length=16), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=True),
        sa.Column("from_version", sa.String(length=64), nullable=True),
        sa.Column("to_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=24), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("bytes_total", sa.Integer(), nullable=True),
        sa.Column("bytes_done", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("log", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_update_record"),
    )
    op.create_index(
        "ix_update_record_target_created_at",
        "update_record",
        ["target", "created_at"],
    )
    # 部分唯一索引：只有 status='running' 的行互斥，历史行不受限。
    # autogenerate 靠模型里的 sqlite_where 带出谓词，这里逐字复核过。
    op.create_index(
        "uq_update_record_running_target",
        "update_record",
        ["target"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
    )

    op.create_table(
        "notify_channel",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("events", sa.JSON(none_as_null=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notify_channel"),
        sa.UniqueConstraint("type", "name", name="uq_notify_channel_type_name"),
    )

    op.create_table(
        "resource_asset",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("path", sa.String(length=255), nullable=True),
        sa.Column("meta", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("remote_version", sa.String(length=64), nullable=True),
        sa.Column("etag", sa.String(length=128), nullable=True),
        sa.Column("last_modified", sa.String(length=64), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "content IS NOT NULL OR path IS NOT NULL",
            name="ck_resource_asset_content_or_path",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resource_asset"),
        sa.UniqueConstraint("kind", "name", name="uq_resource_asset_kind_name"),
    )
    op.create_index(
        "ix_resource_asset_kind_enabled", "resource_asset", ["kind", "enabled"]
    )


def downgrade() -> None:
    """Downgrade schema：按建表逆序删索引与表，13 张表一张不留。"""
    op.drop_index("ix_resource_asset_kind_enabled", table_name="resource_asset")
    op.drop_table("resource_asset")

    op.drop_table("notify_channel")

    op.drop_index(
        "uq_update_record_running_target", table_name="update_record"
    )
    op.drop_index(
        "ix_update_record_target_created_at", table_name="update_record"
    )
    op.drop_table("update_record")

    op.drop_index("ix_confirmation_status_expires_at", table_name="confirmation")
    op.drop_table("confirmation")

    op.drop_index(
        "ix_agent_audit_tool_name_created_at", table_name="agent_audit"
    )
    op.drop_index("ix_agent_audit_caller_created_at", table_name="agent_audit")
    op.drop_table("agent_audit")

    op.drop_table("agent_message")

    op.drop_index(
        "ix_agent_session_last_message_at", table_name="agent_session"
    )
    op.drop_table("agent_session")

    op.drop_table("setting")

    op.drop_index("ix_schedule_enabled_next_run_at", table_name="schedule")
    op.drop_table("schedule")

    op.drop_index(
        "ix_screenshot_pipeline_id_created_at", table_name="screenshot"
    )
    op.drop_index("ix_screenshot_created_at", table_name="screenshot")
    op.drop_table("screenshot")

    op.drop_index("ix_log_entry_pipeline_id_id", table_name="log_entry")
    op.drop_index("ix_log_entry_source_created_at", table_name="log_entry")
    op.drop_table("log_entry")

    op.drop_table("task")

    op.drop_index(
        "ix_pipeline_schedule_id_created_at", table_name="pipeline"
    )
    op.drop_index("ix_pipeline_source_created_at", table_name="pipeline")
    op.drop_index("ix_pipeline_status_created_at", table_name="pipeline")
    op.drop_index("ix_pipeline_dequeue", table_name="pipeline")
    op.drop_table("pipeline")
