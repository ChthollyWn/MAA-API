"""表定义验收：业务表、命名约定、§6 索引、约束语义（docs/04 §3/§5/§6/§7）。

测试纪律：

- 用同步测试函数 + 临时库（``create_all`` 只用于测试；生产建表永远走 Alembic，
  docs/04 §8.3），绝不写仓库 ``resource/``。
- 元数据断言（表/列/索引/外键）与 DDL 断言（``sqlite_master``）各查一遍：前者
  保证模型声明正确，后者保证 SQLite 真的落下了 ``AUTOINCREMENT``、部分索引谓词、
  ``ON DELETE`` 与 ``CHECK`` —— 这几个失败模式都不会报错，只会静默少东西。
- 约束语义用真插入验证：级联删除、部分唯一互斥、CHECK 二选一。
"""

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    Integer,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from maa_api.db import models
from maa_api.db.models import utcnow

MD = SQLModel.metadata
SQLITE_DIALECT = sqlite_dialect.dialect()

BUSINESS_TABLES = {
    "pipeline",
    "task",
    "stage_drop",
    "sanity_observation",
    "log_entry",
    "screenshot",
    "schedule",
    "setting",
    "agent_session",
    "agent_message",
    "agent_audit",
    "agent_idempotency",
    "confirmation",
    "update_record",
    "notify_channel",
    "resource_asset",
    "api_snippet",
    "push_subscription",
    "agent_knowledge",
    "agent_question",
    "account_operator",
    "account_operator_diff",
    "account_depot_item",
    "account_recognition",
    "upgrade_plan",
    "copilot_grant",
    "skland_credential",
    "skland_role",
    "agent_run",
    # M67：模型档案与成本账本（v3 docs/03 §4.1、§4.2）。
    "model_profile",
    "cost_ledger",
    # M68：运行追踪（v3 docs/03 §5.1）。
    "trace_span",
    # M69：工单领域五表（v3 M69 规格 §4.1）。
    "ticket",
    "mandate",
    "ticket_report",
    "thread_subscription",
    "agent_inbox",
    # M71：设备租约（v3 M71 规格 §4.1）。
    "device_lease",
    # M74：压缩检查点（v3 M74 规格 §4.1）。
    "compaction_checkpoint",
    # M75：产物与计划（v3 docs/03 §8.2、§8.3）。
    "artifact",
    "run_plan",
    # M76：检索索引三表（v3 docs/03 §9）。FTS 虚拟表与影子表不进业务表。
    "knowledge_source",
    "knowledge_document",
    "knowledge_chunk",
    # M78：技能覆盖层（v3 specs/m78 §4.1）。
    "agent_skill",
    # M79：长期记忆（v3 specs/m79 §4.1、docs/v3/03 §10.2）。
    "memory_item",
    # M81：事件源三表（v3 specs/m81 §4.1、docs/v3/03 §11）。
    "agent_trigger",
    "trigger_rule",
    "trigger_observation",
    # M82：目标与里程碑（v3 specs/m82 §4.1、docs/v3/03 §11.4）。
    "goal",
    "goal_milestone",
    # M83：值守告警（v3 specs/m83 §4.1、docs/v3/03 §11.5）。
    "watchdog_alert",
    # M84：每日消耗账本（v3 specs/m84 §4.1、docs/v3/03 §11.6）。
    "spend_ledger",
    # M85：IM 通道三表（v3 specs/m85 §4.1、docs/v3/03 §11.7）。
    "channel_binding",
    "channel_bind_code",
    "outbox",
}
UUID_PK_TABLES = [
    "pipeline",
    "task",
    "screenshot",
    "schedule",
    "confirmation",
    "update_record",
    "notify_channel",
    "agent_session",
    "resource_asset",
    "api_snippet",
    "push_subscription",
    "agent_question",
    "copilot_grant",
    "agent_run",
    "model_profile",
    # M69：工单与委托的 id 是对外暴露的 36 字符 UUID。
    "ticket",
    "mandate",
    # M74：检查点 id 对外暴露（SSE / WS 都带它）。
    "compaction_checkpoint",
    # M75：产物与计划 id 对外暴露，是 36 字符 UUID。
    "artifact",
    "run_plan",
    # M76：文档 id 是对外暴露的 36 字符 UUID；来源 id 是短名字，分块是自增主键。
    "knowledge_document",
    # M79：记忆 id 对外暴露（审核/更新/删除端点都在路径里）。
    "memory_item",
    # M82：目标与里程碑 id 都对外暴露（详情页与复盘汇报都按它引用）。
    "goal",
    "goal_milestone",
    # M85：绑定 id 对外暴露（解绑端点路径里带它）；绑定码是 16 字符短码。
    "channel_binding",
]
AUTOINCREMENT_TABLES = [
    "log_entry",
    "agent_message",
    "agent_audit",
    "agent_idempotency",
    "cost_ledger",
    "trace_span",
    # M69：汇报、订阅与收件箱都是高频追加表。
    "ticket_report",
    "thread_subscription",
    "agent_inbox",
    # M76：分块的 id 同时是 FTS5 的 rowid，清理后不能复用。
    "knowledge_chunk",
    # M81：事件与观察记录都是高频追加表。
    "agent_trigger",
    "trigger_observation",
    # M83：告警是高频追加表，重复命中只更新证据、不换行（spec §4.1）。
    "watchdog_alert",
    # M84：消耗账本只增不删，整数自增主键（spec §4.1）。
    "spend_ledger",
    # M85：发件箱只增不删，整数自增主键（spec §4.1）。
    "outbox",
]

# §6 索引清单：名字 → (表, 列序, 是否唯一)。列序=等值→范围→排序，照抄不重排。
EXPECTED_INDEXES = {
    "ix_pipeline_dequeue": (
        "pipeline",
        ("core_id", "status", "priority", "created_at"),
        False,
    ),
    "ix_pipeline_status_created_at": ("pipeline", ("status", "created_at"), False),
    "ix_pipeline_source_created_at": ("pipeline", ("source", "created_at"), False),
    "ix_pipeline_schedule_id_created_at": (
        "pipeline",
        ("schedule_id", "created_at"),
        False,
    ),
    "ix_stage_drop_stage_created_at": (
        "stage_drop",
        ("stage_code", "created_at"),
        False,
    ),
    "ix_stage_drop_callback_id": ("stage_drop", ("callback_id",), False),
    # M27：账号数据的 3 条索引（两张几百行的表按 docs/04 §6 不建索引）。
    "ix_account_recognition_kind_recognized_at": (
        "account_recognition",
        ("kind", "recognized_at"),
        False,
    ),
    "ix_account_operator_diff_dismissed_at_created_at": (
        "account_operator_diff",
        ("dismissed_at", "created_at"),
        False,
    ),
    "ix_upgrade_plan_status_created_at": (
        "upgrade_plan",
        ("status", "created_at"),
        False,
    ),
    # M29：作业授权的一条部分唯一索引 + 两条复合索引。
    "uq_copilot_grant_active_session": (
        "copilot_grant",
        ("session_id",),
        True,
    ),
    "ix_copilot_grant_status_expires_at": (
        "copilot_grant",
        ("status", "expires_at"),
        False,
    ),
    "ix_copilot_grant_session_id_created_at": (
        "copilot_grant",
        ("session_id", "created_at"),
        False,
    ),
    "ix_sanity_observation_stage_created_at": (
        "sanity_observation",
        ("stage_code", "created_at"),
        False,
    ),
    "ix_log_entry_source_created_at": ("log_entry", ("source", "created_at"), False),
    "ix_log_entry_pipeline_id_id": ("log_entry", ("pipeline_id", "id"), False),
    "ix_screenshot_created_at": ("screenshot", ("created_at",), False),
    "ix_push_subscription_disabled_at": ("push_subscription", ("disabled_at",), False),
    "ix_screenshot_pipeline_id_created_at": (
        "screenshot",
        ("pipeline_id", "created_at"),
        False,
    ),
    "ix_schedule_enabled_next_run_at": ("schedule", ("enabled", "next_run_at"), False),
    "ix_confirmation_status_expires_at": (
        "confirmation",
        ("status", "expires_at"),
        False,
    ),
    "ix_agent_audit_caller_created_at": ("agent_audit", ("caller", "created_at"), False),
    "ix_agent_audit_tool_name_created_at": (
        "agent_audit",
        ("tool_name", "created_at"),
        False,
    ),
    # M71：工单审计流（agent_audit 的索引加唯一约束合计正好 3 条）。
    "ix_agent_audit_ticket_id_created_at": (
        "agent_audit",
        ("ticket_id", "created_at"),
        False,
    ),
    "ix_agent_idempotency_created_at": (
        "agent_idempotency",
        ("created_at",),
        False,
    ),
    "ix_agent_session_last_message_at": (
        "agent_session",
        ("last_message_at",),
        False,
    ),
    "uq_agent_session_incident_key": (
        "agent_session",
        ("incident_key",),
        True,
    ),
    "ix_update_record_target_created_at": (
        "update_record",
        ("target", "created_at"),
        False,
    ),
    "ix_resource_asset_kind_enabled": ("resource_asset", ("kind", "enabled"), False),
    "ix_api_snippet_updated_at": ("api_snippet", ("updated_at",), False),
    "ix_agent_question_status_expires_at": (
        "agent_question",
        ("status", "expires_at"),
        False,
    ),
    "ix_agent_question_session_status_created_at": (
        "agent_question",
        ("session_id", "status", "created_at"),
        False,
    ),
    "uq_update_record_running_target": ("update_record", ("target",), True),
    # M66：运行树的 5 条索引（agent_session 一条、agent_message 一条、agent_run 三条）。
    "ix_agent_session_kind_status": ("agent_session", ("kind", "status"), False),
    "ix_agent_message_run_id": ("agent_message", ("run_id",), False),
    "uq_agent_run_active_session": ("agent_run", ("session_id",), True),
    "ix_agent_run_session_id_started_at": (
        "agent_run",
        ("session_id", "started_at"),
        False,
    ),
    "ix_agent_run_root_run_id": ("agent_run", ("root_run_id",), False),
    # M67：成本账本的两条索引（按时间清理 / 区间汇总、按代理分组汇总）。
    "ix_cost_ledger_created_at": ("cost_ledger", ("created_at",), False),
    "ix_cost_ledger_agent_name_created_at": (
        "cost_ledger",
        ("agent_name", "created_at"),
        False,
    ),
    # M68：运行追踪的两条索引（按运行取整条 trace、按保留期清理）。
    "ix_trace_span_run_id": ("trace_span", ("run_id",), False),
    "ix_trace_span_started_at": ("trace_span", ("started_at",), False),
    # M69：工单列表/汇报流/订阅/收件箱各一条查询索引。
    "ix_ticket_status_updated_at": ("ticket", ("status", "updated_at"), False),
    "ix_ticket_kind_created_at": ("ticket", ("kind", "created_at"), False),
    "ix_ticket_report_ticket_id_created_at": (
        "ticket_report",
        ("ticket_id", "created_at"),
        False,
    ),
    "ix_thread_subscription_thread_key_active": (
        "thread_subscription",
        ("thread_key", "active"),
        False,
    ),
    "ix_agent_inbox_session_id_status_id": (
        "agent_inbox",
        ("session_id", "status", "id"),
        False,
    ),
    # M74：按会话取检查点列表（created_at 降序）。
    "ix_compaction_checkpoint_session_id_created_at": (
        "compaction_checkpoint",
        ("session_id", "created_at"),
        False,
    ),
    # M75：产物按工单/会话时间线列出；计划的部分唯一索引保证同会话只有一条未结束计划。
    "ix_artifact_ticket_id_created_at": (
        "artifact",
        ("ticket_id", "created_at"),
        False,
    ),
    "ix_artifact_session_id_created_at": (
        "artifact",
        ("session_id", "created_at"),
        False,
    ),
    "uq_run_plan_session_id_active": ("run_plan", ("session_id",), True),
    # M76：按文档取有序分块、按向量模型找缺向量的分块（v3 docs/03 §9.3）。
    "ix_knowledge_chunk_document_id_ord": (
        "knowledge_chunk",
        ("document_id", "ord"),
        False,
    ),
    "ix_knowledge_chunk_embedding_model": (
        "knowledge_chunk",
        ("embedding_model",),
        False,
    ),
    # M79：记忆列表按状态/种类过滤、按分类键过滤去重（v3 docs/03 §10.2）。
    "ix_memory_item_status_kind": ("memory_item", ("status", "kind"), False),
    "ix_memory_item_subject": ("memory_item", ("subject",), False),
    # M81：事件去重（部分唯一）、话题近期事件、清理扫描；观察按规则与时间列。
    "uq_agent_trigger_dedupe_key": ("agent_trigger", ("dedupe_key",), True),
    "ix_agent_trigger_thread_key_created_at": (
        "agent_trigger",
        ("thread_key", "created_at"),
        False,
    ),
    "ix_agent_trigger_created_at": ("agent_trigger", ("created_at",), False),
    "ix_trigger_observation_rule_id_created_at": (
        "trigger_observation",
        ("rule_id", "created_at"),
        False,
    ),
    # M82：目标列表按状态+更新时间；里程碑按目标与序号取。
    "ix_goal_status_updated_at": ("goal", ("status", "updated_at"), False),
    "ix_goal_milestone_goal_id_ord": ("goal_milestone", ("goal_id", "ord"), False),
    # M83：告警列表按时间倒序；未解除告警按目标去重（spec §4.1）。
    "ix_watchdog_alert_created_at": ("watchdog_alert", ("created_at",), False),
    "ix_watchdog_alert_target_kind_target_id": (
        "watchdog_alert",
        ("target_kind", "target_id"),
        False,
    ),
    # M84：按种类在消耗日窗口内汇总用量（spec §4.1）。
    "ix_spend_ledger_kind_created_at": (
        "spend_ledger",
        ("kind", "created_at"),
        False,
    ),
    # M85：同一平台上的同一用户与同一会话只绑定一次；发件箱按状态与到期时间取件。
    "uq_channel_binding_platform_external_user_id_external_chat_id": (
        "channel_binding",
        ("platform", "external_user_id", "external_chat_id"),
        True,
    ),
    "ix_outbox_status_next_attempt_at": (
        "outbox",
        ("status", "next_attempt_at"),
        False,
    ),
}

# §5 唯一约束：名字 → (表, 列)
EXPECTED_UNIQUE_CONSTRAINTS = {
    "uq_pipeline_idempotency_key": ("pipeline", ("idempotency_key",)),
    "uq_task_pipeline_order": ("task", ("pipeline_id", "order_index")),
    "uq_task_pipeline_maa_task": ("task", ("pipeline_id", "maa_task_id")),
    "uq_schedule_name": ("schedule", ("name",)),
    "uq_agent_message_session_seq": ("agent_message", ("session_id", "seq")),
    "uq_agent_idempotency_caller_key": ("agent_idempotency", ("caller", "key")),
    "uq_notify_channel_type_name": ("notify_channel", ("type", "name")),
    "uq_resource_asset_kind_name": ("resource_asset", ("kind", "name")),
    "uq_api_snippet_name": ("api_snippet", ("name",)),
    "uq_push_subscription_endpoint": ("push_subscription", ("endpoint",)),
    # M67：档案名唯一（default 档案不入表，不在这里）。
    "uq_model_profile_name": ("model_profile", ("name",)),
    # M68：同一条 trace 里的 span id 唯一。
    "uq_trace_span_trace_id_span_id": ("trace_span", ("trace_id", "span_id")),
    # M69：一个工单一份委托；同一工单对同一话题键只有一行订阅。
    "uq_mandate_ticket_id": ("mandate", ("ticket_id",)),
    "uq_thread_subscription_ticket_id_thread_key": (
        "thread_subscription",
        ("ticket_id", "thread_key"),
    ),
    # M71：租约 id 对外暴露，必须唯一（device_id 主键保证容量 1）。
    "uq_device_lease_lease_id": ("device_lease", ("lease_id",)),
    # M76：同一来源里 external_id 唯一（条目 id、页面标题或 URL）。
    "uq_knowledge_document_source_id_external_id": (
        "knowledge_document",
        ("source_id", "external_id"),
    ),
    # M85：发件箱按去重键唯一，重复入队返回旧行（spec §4.5）。
    "uq_outbox_dedupe_key": ("outbox", ("dedupe_key",)),
}

# 外键级联：列 → ondelete（docs/04 §3.4）
EXPECTED_FK_CASCADE = {
    ("task", "pipeline_id"),
    ("agent_message", "session_id"),
    ("agent_idempotency", "audit_id"),
    ("agent_question", "session_id"),
    # M66：运行随会话删除（规范 §4.1.2）。
    ("agent_run", "session_id"),
    # M68：运行删了，它名下的 span 一起删。
    ("trace_span", "run_id"),
    # M69：工单删了，它的委托、汇报与订阅没有独立存在的意义。
    ("mandate", "ticket_id"),
    ("ticket_report", "ticket_id"),
    ("thread_subscription", "ticket_id"),
    ("agent_inbox", "session_id"),
    # M74：会话删了，它的检查点一起删（检查点没有独立存在的意义）。
    ("compaction_checkpoint", "session_id"),
    # M75：会话删了，它的计划一起删。
    ("run_plan", "session_id"),
    # M76：来源删了文档一起删，文档删了分块一起删。
    ("knowledge_document", "source_id"),
    ("knowledge_chunk", "document_id"),
    # M81：事件删了，它的观察记录一起删。
    ("trigger_observation", "trigger_id"),
    # M82：目标删了，它的里程碑一起删。
    ("goal_milestone", "goal_id"),
}
EXPECTED_FK_SET_NULL = {
    ("pipeline", "schedule_id"),
    ("pipeline", "agent_session_id"),
    ("pipeline", "retry_of_id"),
    ("log_entry", "pipeline_id"),
    ("log_entry", "task_id"),
    ("stage_drop", "pipeline_id"),
    ("stage_drop", "task_id"),
    ("sanity_observation", "pipeline_id"),
    ("sanity_observation", "task_id"),
    ("screenshot", "pipeline_id"),
    ("screenshot", "task_id"),
    ("schedule", "last_pipeline_id"),
    ("agent_session", "atomic_grant_id"),
    ("agent_message", "audit_id"),
    # M27：账号数据的可空外键同样按 SET NULL 处理（识别记录/计划被删不该带走引用方）。
    ("account_recognition", "pipeline_id"),
    ("account_recognition", "task_id"),
    ("upgrade_plan", "schedule_id"),
    ("upgrade_plan", "session_id"),
    ("agent_audit", "confirmation_id"),
    # M29：会话删了授权记录仍要留着（审计），只是不再对任何会话生效。
    ("copilot_grant", "session_id"),
    ("agent_question", "audit_id"),
    ("agent_audit", "authorized_by_id"),
    ("agent_audit", "session_id"),
    ("confirmation", "audit_id"),
    # M66：运行树的自引用与会话→父会话、消息→运行都按 SET NULL 处理。
    ("agent_session", "parent_session_id"),
    ("agent_run", "parent_run_id"),
    ("agent_message", "run_id"),
    # M80：分叉来源会话被删只解除关联，分叉出的会话留着。
    ("agent_session", "forked_from_session_id"),
    # M67：运行与会话删了，成本账本仍要留着（预算与历史汇总）。
    ("cost_ledger", "run_id"),
    ("cost_ledger", "session_id"),
    # M69：工单/收件箱被清理时，历史会话、运行与消息仍要留着，只是不再关联。
    ("ticket", "orchestrator_session_id"),
    ("ticket", "worker_session_id"),
    ("ticket_report", "run_id"),
    ("agent_inbox", "consumed_run_id"),
    ("agent_session", "ticket_id"),
    ("agent_run", "ticket_id"),
    ("agent_message", "inbox_id"),
    # M71：租约随设备/工单/运行清理只解除关联；确认与提问保留发起来源，审计保留归属。
    ("device_lease", "ticket_id"),
    ("device_lease", "run_id"),
    ("confirmation", "origin_run_id"),
    ("confirmation", "origin_ticket_id"),
    ("agent_question", "origin_run_id"),
    ("agent_question", "origin_ticket_id"),
    ("agent_audit", "run_id"),
    ("agent_audit", "ticket_id"),
    # M74：产出摘要的运行被清理时检查点仍要留着，只解除关联。
    ("compaction_checkpoint", "run_id"),
    # M75：产物比会话/工单/运行活得久；计划比工单活得久。
    ("artifact", "session_id"),
    ("artifact", "ticket_id"),
    ("artifact", "run_id"),
    ("run_plan", "ticket_id"),
    # M79：记忆比工单/会话/运行与被取代的旧记忆活得久（03 §14 不自动删）。
    ("memory_item", "source_ticket_id"),
    ("memory_item", "source_session_id"),
    ("memory_item", "source_run_id"),
    ("memory_item", "supersedes_id"),
    # M81：运行/工单/事件被清理时只解除关联；ticket 与 agent_trigger 是循环外键。
    ("agent_trigger", "eval_run_id"),
    ("agent_trigger", "ticket_id"),
    ("ticket", "source_trigger_id"),
    # M82：规划工单/养成计划被清理时只解除关联；ticket 与 goal 是循环外键。
    ("goal", "planning_ticket_id"),
    ("goal", "upgrade_plan_id"),
    ("ticket", "goal_id"),
    # M84：流水线、任务、工单被清理时账本行留着，用量汇总不断（spec §4.1）。
    ("spend_ledger", "pipeline_id"),
    ("spend_ledger", "task_id"),
    ("spend_ledger", "ticket_id"),
    # M85：运行/工单被清理时发件箱行留着，供审计与续发（spec §4.1）。
    ("outbox", "ref_run_id"),
    ("outbox", "ref_ticket_id"),
}

EXPECTED_TEXT_COLUMNS = {
    ("pipeline", "error_message"),
    ("task", "error_message"),
    ("stage_drop", "item_name"),
    ("log_entry", "content"),
    ("agent_message", "content"),
    ("agent_message", "reasoning_content"),
    ("agent_audit", "result_summary"),
    ("confirmation", "reason"),
    ("confirmation", "resolved_reason"),
    ("update_record", "error_message"),
    ("update_record", "log"),
    ("notify_channel", "last_error"),
    ("resource_asset", "description"),
    ("agent_knowledge", "body"),
    ("agent_question", "question"),
    # M42：森空岛凭据的两列密文（Fernet 字符串，长度随明文变化，不能定宽）。
    ("skland_credential", "cred_encrypted"),
    ("skland_credential", "token_encrypted"),
    # M66：运行错误描述与流水线/任务同一写法（长度不定，用 TEXT）。
    ("agent_run", "error_message"),
    # M67：档案密文长度随明文变化，不能定宽。
    ("model_profile", "api_key_encrypted"),
    # M69：工单摘要与委托范围长度不定（scope 最多 2000 字）。
    ("ticket", "summary"),
    ("mandate", "scope"),
    ("ticket_report", "summary"),
    # M74：渲染后的摘要长度不定，用 TEXT。
    ("compaction_checkpoint", "summary"),
    # M75：产物正文长度不定（<=256 KiB 直接进库），用 TEXT。
    ("artifact", "content"),
    # M76：来源错误描述与分块正文长度不定，用 TEXT。
    ("knowledge_source", "error"),
    ("knowledge_chunk", "text"),
    # M78：技能正文长度不定，用 TEXT。
    ("agent_skill", "body"),
    # M79：记忆正文不超过 500 字，但长度按内容变化，用 TEXT（03 §10.2）。
    ("memory_item", "content"),
    # M81：事件的不合法原因与规则研判提示长度不定，用 TEXT。
    ("agent_trigger", "eval_reason"),
    ("trigger_rule", "policy"),
    # M82：目标描述长度不定（目标本身最多 2000 字），用 TEXT。
    ("goal", "description"),
    # M83：告警正文长度不定，用 TEXT。
    ("watchdog_alert", "message"),
    # M85：发件箱最后一次失败原因长度不定，用 TEXT。
    ("outbox", "last_error"),
}

EXPECTED_JSON_COLUMNS = {
    ("task", "params"),
    ("task", "raw_params"),
    ("log_entry", "meta"),
    ("schedule", "template"),
    ("setting", "value"),
    ("agent_message", "tool_calls"),
    ("agent_audit", "arguments"),
    ("agent_audit", "scopes"),
    ("agent_audit", "result_ref"),
    ("agent_idempotency", "response_body"),
    ("confirmation", "payload"),
    ("notify_channel", "config"),
    ("notify_channel", "events"),
    ("resource_asset", "content"),
    # M27：账号数据的 JSON 列。
    ("account_operator", "masteries"),
    ("account_operator", "modules"),
    # M43：卡片用的两列也是 JSON。
    ("account_operator", "skill_ids"),
    ("account_operator", "module_details"),
    # M45 W3a：仓库两路合并的来源表与识别详情。
    ("account_depot_item", "sources"),
    ("account_recognition", "detail"),
    ("account_operator", "recognized_values"),
    ("account_operator", "field_sources"),
    ("account_operator_diff", "manual_value"),
    ("account_operator_diff", "recognized_value"),
    ("upgrade_plan", "targets"),
    ("upgrade_plan", "requirements"),
    ("upgrade_plan", "progress"),
    ("resource_asset", "meta"),
    ("api_snippet", "path_params"),
    ("api_snippet", "query"),
    ("api_snippet", "headers"),
    ("api_snippet", "body"),
    ("agent_question", "options"),
    ("agent_question", "selection"),
    # M66：运行的自由结构元数据（成本、简历等由服务层定义）。
    ("agent_run", "meta"),
    # M68：span 的属性字典（v3 10 §2 定义键，表不做约束）。
    ("trace_span", "attributes"),
    # M69：工单的人审动作/结果、汇报的载荷与收件箱事件。
    ("ticket", "human_action"),
    ("ticket", "result"),
    ("ticket_report", "human_action"),
    ("ticket_report", "details"),
    ("agent_inbox", "payload"),
    # M74：结构性摘要与压缩时收集的 durable 状态。
    ("compaction_checkpoint", "summary_json"),
    ("compaction_checkpoint", "durable"),
    # M75：产物的自由结构元数据与计划的步骤列表。
    ("artifact", "meta"),
    ("run_plan", "items"),
    # M76：来源的配置与文档的自由结构元数据（服务层定义键）。
    ("knowledge_source", "config"),
    ("knowledge_document", "meta"),
    # M78：技能的工具与适用代理白名单。
    ("agent_skill", "tools"),
    ("agent_skill", "agents"),
    # M81：事件载荷、规则条件与观察记录里的判定草稿。
    ("agent_trigger", "payload"),
    ("trigger_rule", "conditions"),
    ("trigger_observation", "decision"),
    # M82：目标的练度目标与复盘快照、关联的定时任务、里程碑指标。
    ("goal", "target"),
    ("goal", "schedule_ids"),
    ("goal", "progress"),
    ("goal_milestone", "metrics"),
    # M83：告警的判定证据（重复命中时写 evidence.latest）。
    ("watchdog_alert", "evidence"),
    # M85：发件箱的发送内容与平台回执。
    ("outbox", "payload"),
    ("outbox", "receipt"),
}

# FTS5 对象不属于 SQLModel 元数据，也不属于业务表：``create_all`` 路径由
# ``models.KNOWLEDGE_FTS_DDL`` 的监听器建，生产路径由迁移 0030 建。
# 名字 → sqlite_master.type。
FTS_OBJECTS = {
    "knowledge_chunk_fts": "table",
    "knowledge_chunk_ai": "trigger",
    "knowledge_chunk_ad": "trigger",
    "knowledge_chunk_au": "trigger",
}
# FTS5 虚拟表自己的影子表；随虚拟表一起由 SQLite 创建/删除。
FTS_SHADOW_TABLES = {
    "knowledge_chunk_fts_data",
    "knowledge_chunk_fts_idx",
    "knowledge_chunk_fts_docsize",
    "knowledge_chunk_fts_config",
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _enable_foreign_keys(dbapi_conn, _record=None) -> None:
    """SQLite 默认关闭外键约束，级联语义测试必须显式打开。"""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "models.db"


@pytest.fixture
def engine(db_path):
    eng = create_engine(f"sqlite:///{db_path}")
    event.listen(eng, "connect", _enable_foreign_keys)
    MD.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine, db_path):
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA foreign_keys=ON")
    yield raw
    raw.close()


def ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("select sql from sqlite_master where name=?", (name,)).fetchone()
    assert row is not None and row[0], f"sqlite_master 里没有 {name}"
    return row[0].upper()


def sql_type(column) -> str:
    """列在 SQLite 上的 DDL 类型，如 ``VARCHAR(36)`` / ``TEXT`` / ``JSON``。

    SQLModel 的字符串列是 ``AutoString``（``TypeDecorator``），不是 ``String``
    子类，``isinstance`` 判断会失败；按编译结果断言最可靠。
    """
    return str(column.type.compile(dialect=SQLITE_DIALECT)).upper()


def make_pipeline(session: Session, **kwargs) -> models.Pipeline:
    kwargs.setdefault("source", "manual")
    kwargs.setdefault("priority", 0)
    kwargs.setdefault("task_count", 1)
    pipeline = models.Pipeline(**kwargs)
    session.add(pipeline)
    session.commit()
    return pipeline


# ---------------------------------------------------------------------------
# 表集合与主键形态
# ---------------------------------------------------------------------------
def test_metadata_has_exactly_56_business_tables():
    """56 张业务表；alembic_version 由 Alembic 维护，FTS 对象与影子表不计入。"""
    assert set(MD.tables) == BUSINESS_TABLES
    # 排除的办法就是不把 FTS 对象放进元数据：迁移比对只遍历这个集合。
    fts_names = set(FTS_OBJECTS) | FTS_SHADOW_TABLES
    assert not (set(MD.tables) & fts_names)


def test_create_all_also_creates_fts_objects(engine, db_path):
    """``create_all`` 路径也建出 FTS5 虚拟表与三个触发器，且写入即进索引。"""
    with sqlite3.connect(db_path) as conn:
        params = ", ".join("?" * len(FTS_OBJECTS))
        actual = dict(
            conn.execute(
                f"select name, type from sqlite_master where name in ({params})",
                tuple(FTS_OBJECTS),
            ).fetchall()
        )
        assert actual == FTS_OBJECTS
        shadow = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type='table'"
                " and name like 'knowledge_chunk_fts_%'"
            )
        }
        assert shadow == FTS_SHADOW_TABLES

        conn.execute(
            "insert into knowledge_source (id, kind, title, enabled, trusted,"
            " config, status, doc_count, chunk_count, created_at, updated_at)"
            " values ('builtin', 'builtin', '内置知识', 1, 1, '{}', 'idle', 1, 1,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "insert into knowledge_document (id, source_id, external_id, title,"
            " content_hash, meta, created_at, updated_at)"
            " values ('doc-1', 'builtin', 'entry-1', '掉落与刷图效率', 'hash', '{}',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.execute(
            "insert into knowledge_chunk (document_id, ord, heading, text,"
            " char_count, created_at)"
            " values ('doc-1', 0, '常驻最优图的判断', '以 1-7 刷固源岩为推荐做法', 14,"
            " '2026-01-01 00:00:00')"
        )
        conn.commit()

        rowids = [
            row[0]
            for row in conn.execute(
                "select rowid from knowledge_chunk_fts"
                " where knowledge_chunk_fts match '\"固源岩\"'"
            )
        ]
        chunk_id = conn.execute("select id from knowledge_chunk").fetchone()[0]
        assert rowids == [chunk_id]


def test_knowledge_chunk_embedding_is_blob():
    """向量列是 BLOB（float32 小端序）且可空；没有向量时为空。"""
    column = MD.tables["knowledge_chunk"].c.embedding
    assert sql_type(column) == "BLOB"
    assert column.nullable is True


def test_agent_idempotency_request_mode_is_nullable_for_legacy_rows():
    column = MD.tables["agent_idempotency"].c.request_mode
    assert column.nullable is True
    assert sql_type(column) == "VARCHAR(8)"


@pytest.mark.parametrize("table", UUID_PK_TABLES)
def test_uuid_primary_key_is_varchar_36(table):
    col = MD.tables[table].c["id"]
    assert col.primary_key
    assert sql_type(col) == "VARCHAR(36)"
    assert MD.tables[table].dialect_options["sqlite"].get("autoincrement") is not True


@pytest.mark.parametrize("table", AUTOINCREMENT_TABLES)
def test_high_frequency_tables_use_autoincrement(table):
    table_obj = MD.tables[table]
    col = table_obj.c["id"]
    assert col.primary_key
    assert isinstance(col.type, Integer)
    # __table_args__ 里的 sqlite_autoincrement 是 DDL 落下关键字的唯一开关（M2-01 实测）
    assert table_obj.dialect_options["sqlite"].get("autoincrement") is True


@pytest.mark.parametrize("table", AUTOINCREMENT_TABLES)
def test_autoincrement_reaches_ddl(conn, table):
    assert "AUTOINCREMENT" in ddl(conn, table)


def test_ids_are_not_reused_after_tail_delete(conn):
    """清理删到尾部后 id 不回退 —— WebSocket 续传游标的前提（docs/04 §3.1）。"""
    for content in ("one", "two", "three"):
        conn.execute(
            "insert into log_entry (source, level, content, created_at) values (?,?,?,?)",
            ("server", "info", content, "2026-01-01 00:00:00"),
        )
    conn.commit()
    last = conn.execute("select max(id) from log_entry").fetchone()[0]
    conn.execute("delete from log_entry where id = ?", (last,))
    conn.commit()
    conn.execute(
        "insert into log_entry (source, level, content, created_at) values (?,?,?,?)",
        ("server", "info", "after-cleanup", "2026-01-01 00:00:01"),
    )
    conn.commit()
    new_id = conn.execute("select max(id) from log_entry").fetchone()[0]
    assert new_id > last  # 没有复用被删掉的 id
    assert conn.execute(
        "select name from sqlite_master where name='sqlite_sequence'"
    ).fetchone() is not None


def test_uuid_primary_keys_are_unique_36_char_strings(engine):
    with Session(engine) as session:
        first = make_pipeline(session)
        second = make_pipeline(session)
        assert first.id != second.id
        assert len(first.id) == 36
        uuid.UUID(first.id)  # 可解析


# ---------------------------------------------------------------------------
# 命名约定（docs/04 §8.1）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("table", sorted(BUSINESS_TABLES))
def test_primary_key_follows_naming_convention(table):
    assert MD.tables[table].primary_key.name == f"pk_{table}"


def test_foreign_key_names_follow_naming_convention():
    for table in MD.tables.values():
        for fk in table.foreign_keys:
            expected = (
                f"fk_{table.name}_{fk.parent.name}_{fk.column.table.name}"
            )
            assert fk.constraint.name == expected, (table.name, fk.parent.name)


def test_check_constraint_follows_naming_convention():
    names = {
        c.name
        for c in MD.tables["resource_asset"].constraints
        if isinstance(c, CheckConstraint)
    }
    assert names == {"ck_resource_asset_content_or_path"}


# ---------------------------------------------------------------------------
# 索引与唯一约束（docs/04 §6）
# ---------------------------------------------------------------------------
def test_index_inventory_matches_spec_exactly():
    """63 条索引逐条核对名字、表、列序、唯一性；且没有多建任何一条。"""
    actual = {}
    for table in MD.tables.values():
        for index in table.indexes:
            actual[index.name] = (
                table.name,
                tuple(col.name for col in index.columns),
                bool(index.unique),
            )
    assert actual == EXPECTED_INDEXES


def test_unique_constraints_match_spec():
    actual = {}
    for table in MD.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                actual[constraint.name] = (
                    table.name,
                    tuple(col.name for col in constraint.columns),
                )
    assert actual == EXPECTED_UNIQUE_CONSTRAINTS


def test_copilot_grant_uses_partial_unique_index_for_one_active_per_session(conn):
    """每个会话同时最多一个 active：部分唯一索引，不是普通唯一约束（规格 §9.6）。

    普通唯一索引会让同一会话的历史授权只能留一条；谓词写成 ``status = 'active'``
    之后终态历史可以堆积。
    """
    table = MD.tables["copilot_grant"]
    unique = [ix for ix in table.indexes if ix.name == "uq_copilot_grant_active_session"]
    assert len(unique) == 1 and unique[0].unique
    assert unique[0].dialect_options["sqlite"].get("where") is not None
    assert "WHERE" in ddl(conn, "uq_copilot_grant_active_session")
    assert "STATUS = 'ACTIVE'" in ddl(conn, "uq_copilot_grant_active_session")
    assert [
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ] == []


def test_partial_unique_index_allows_terminal_history_for_one_session(engine):
    """语义验证：同一会话可以有多条终态授权，但同时只能有一条 active。"""
    with Session(engine) as session:
        session.add(models.AgentSession(id="session-1", model="gpt-test"))
        session.add(
            models.CopilotGrant(
                id="grant-1",
                session_id="session-1",
                stage_id="main_01-07",
                status="succeeded",
                expires_at=utcnow(),
            )
        )
        session.add(
            models.CopilotGrant(
                id="grant-2",
                session_id="session-1",
                stage_id="main_01-07",
                status="active",
                expires_at=utcnow(),
            )
        )
        session.commit()

    with Session(engine) as session:
        session.add(
            models.CopilotGrant(
                id="grant-3",
                session_id="session-1",
                stage_id="main_01-07",
                status="active",
                expires_at=utcnow(),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        session.add(
            models.CopilotGrant(
                id="grant-4",
                session_id="session-1",
                stage_id="main_01-07",
                status="revoked",
                expires_at=utcnow(),
            )
        )
        session.commit()

    with Session(engine) as session:
        rows = session.scalars(select(models.CopilotGrant)).all()
        # 被拒绝的那条没有落库（回滚了），终态历史行照常留着。
        assert sorted(row.id for row in rows) == ["grant-1", "grant-2", "grant-4"]
        assert sum(1 for row in rows if row.status == "active") == 1


def test_copilot_grant_orm_defaults(engine):
    """状态机的默认值：active / 3 次 / 不用掉 / 三星标准 / 允许助战。"""
    with Session(engine) as session:
        session.add(models.AgentSession(id="session-1", model="gpt-test"))
        session.add(
            models.CopilotGrant(
                id="grant-1",
                session_id="session-1",
                stage_id="main_01-07",
                expires_at=utcnow(),
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(models.CopilotGrant, "grant-1")
        assert row.status == "active"
        assert row.attempts == 3
        assert row.used == 0
        assert row.success == "three_star"
        assert row.support == "fill_one"
        assert row.stage_code is None
        assert row.finished_at is None
        assert row.created_at.tzinfo is None


def test_no_standalone_low_cardinality_indexes():
    """status / source / level / kind 这类低基数单列只作复合索引首列（§6 末尾）。"""
    low_cardinality = {"status", "source", "level", "kind", "type", "enabled"}
    offenders = [
        index.name
        for table in MD.tables.values()
        for index in table.indexes
        if len(index.columns) == 1 and next(iter(index.columns)).name in low_cardinality
    ]
    assert offenders == []


@pytest.mark.parametrize("table", AUTOINCREMENT_TABLES)
def test_hot_write_tables_keep_index_budget(table):
    """高频写表索引总数 ≤ 3（索引 + 唯一约束）。"""
    table_obj = MD.tables[table]
    uniques = sum(
        1 for c in table_obj.constraints if isinstance(c, UniqueConstraint)
    )
    assert len(table_obj.indexes) + uniques <= 3


def test_update_record_uses_partial_unique_index(conn):
    table = MD.tables["update_record"]
    running = [i for i in table.indexes if i.name == "uq_update_record_running_target"]
    assert len(running) == 1 and running[0].unique
    assert running[0].dialect_options["sqlite"].get("where") is not None
    assert "WHERE" in ddl(conn, "uq_update_record_running_target")
    assert "STATUS = 'RUNNING'" in ddl(conn, "uq_update_record_running_target")
    # target 上不能另有普通唯一索引/约束，否则历史记录只能留一条
    assert [
        i.name for i in table.indexes if i.unique and i.name != "uq_update_record_running_target"
    ] == []
    assert [
        c.name for c in table.constraints if isinstance(c, UniqueConstraint)
    ] == []


def test_agent_run_uses_partial_unique_index(conn, engine):
    """同一会话至多一条 running / waiting_human 运行，终态历史可堆积（M66 §4.1.2）。"""
    table = MD.tables["agent_run"]
    active = [i for i in table.indexes if i.name == "uq_agent_run_active_session"]
    assert len(active) == 1 and active[0].unique
    assert active[0].dialect_options["sqlite"].get("where") is not None
    assert "WHERE" in ddl(conn, "uq_agent_run_active_session")
    assert "STATUS IN ('RUNNING', 'WAITING_HUMAN')" in ddl(
        conn, "uq_agent_run_active_session"
    )

    with Session(engine) as session:
        session.add(models.AgentSession(id="session-run", model="gpt-test"))
        session.commit()

    # 运行与消息的外键没有 ORM relationship，先落会话再落运行。
    with Session(engine) as session:
        session.add(
            models.AgentRun(
                id="run-succeeded",
                session_id="session-run",
                root_run_id="run-succeeded",
                agent_name="legacy_chat",
                kind="legacy_turn",
                status="succeeded",
                boot_id="boot-1",
            )
        )
        session.add(
            models.AgentRun(
                id="run-running",
                session_id="session-run",
                root_run_id="run-running",
                agent_name="legacy_chat",
                kind="legacy_turn",
                boot_id="boot-1",
            )
        )
        session.commit()

    with Session(engine) as session:
        session.add(
            models.AgentRun(
                id="run-waiting",
                session_id="session-run",
                root_run_id="run-waiting",
                agent_name="legacy_chat",
                kind="legacy_turn",
                status="waiting_human",
                boot_id="boot-1",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # session_id 为空的运行（研判/摘要等）不受约束：多个 NULL 不互斥。
    with Session(engine) as session:
        for index in range(2):
            session.add(
                models.AgentRun(
                    id=f"run-global-{index}",
                    root_run_id=f"run-global-{index}",
                    agent_name="legacy_incident",
                    kind="legacy_turn",
                    boot_id="boot-1",
                )
            )
        session.commit()
        active_global = session.scalars(
            select(models.AgentRun).where(models.AgentRun.session_id.is_(None))
        ).all()
        assert len(active_global) == 2


# ---------------------------------------------------------------------------
# 外键与级联（docs/04 §3.4）
# ---------------------------------------------------------------------------
def test_foreign_key_ondelete_map():
    ondelete = {
        (table.name, fk.parent.name): fk.ondelete
        for table in MD.tables.values()
        for fk in table.foreign_keys
    }
    for key in EXPECTED_FK_CASCADE:
        assert ondelete[key] == "CASCADE", (key, ondelete.get(key))
    for key in EXPECTED_FK_SET_NULL:
        assert ondelete[key] == "SET NULL", (key, ondelete.get(key))


def test_ondelete_reaches_ddl(conn):
    assert "ON DELETE CASCADE" in ddl(conn, "task")
    assert "ON DELETE SET NULL" in ddl(conn, "log_entry")
    assert "ON DELETE SET NULL" in ddl(conn, "screenshot")
    assert "ON DELETE CASCADE" in ddl(conn, "agent_message")


def test_deleting_pipeline_cascades_tasks_and_nulls_logs(engine):
    with Session(engine) as session:
        pipeline = make_pipeline(session)
        task = models.Task(
            pipeline_id=pipeline.id,
            order_index=0,
            type_name="Fight",
            task_name="刷理智",
        )
        session.add(task)
        session.commit()
        log = models.LogEntry(
            source="server",
            level="info",
            content="hello",
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        session.add(log)
        session.commit()
        pipeline_id, task_id, log_id = pipeline.id, task.id, log.id

        session.delete(pipeline)
        session.commit()

    with Session(engine) as session:
        assert session.get(models.Pipeline, pipeline_id) is None
        assert session.get(models.Task, task_id) is None
        orphan = session.get(models.LogEntry, log_id)
        assert orphan is not None  # 日志比主体活得久
        assert orphan.pipeline_id is None
        assert orphan.task_id is None


def test_deleting_agent_session_cascades_messages(engine):
    with Session(engine) as session:
        agent_session = models.AgentSession(model="gpt-test")
        session.add(agent_session)
        session.commit()
        message = models.AgentMessage(
            session_id=agent_session.id, seq=0, role="user", content="hi"
        )
        session.add(message)
        session.commit()
        session_id, message_id = agent_session.id, message.id

        session.delete(agent_session)
        session.commit()

    with Session(engine) as session:
        assert session.get(models.AgentSession, session_id) is None
        assert session.get(models.AgentMessage, message_id) is None


def test_deleting_session_cascades_runs_and_nulls_message_run_id(engine):
    """运行随会话删除；删运行只把消息的 run_id 置空（M66 §4.1.2）。"""
    with Session(engine) as session:
        session.add(models.AgentSession(id="session-kept", model="gpt-test"))
        session.add(models.AgentSession(id="session-doomed", model="gpt-test"))
        session.commit()

    # 运行按表名排序会先于 agent_session 插入，先落会话再落运行。
    with Session(engine) as session:
        session.add(
            models.AgentRun(
                id="run-kept",
                session_id="session-kept",
                root_run_id="run-kept",
                agent_name="legacy_chat",
                kind="legacy_turn",
                boot_id="boot-1",
            )
        )
        session.add(
            models.AgentRun(
                id="run-doomed",
                session_id="session-doomed",
                root_run_id="run-doomed",
                agent_name="legacy_chat",
                kind="legacy_turn",
                boot_id="boot-1",
            )
        )
        session.commit()

    # 运行与消息的外键没有 ORM relationship，先落会话与运行再落消息。
    with Session(engine) as session:
        session.add(
            models.AgentMessage(
                session_id="session-kept",
                seq=0,
                role="assistant",
                content="kept",
                run_id="run-kept",
            )
        )
        session.add(
            models.AgentMessage(
                session_id="session-doomed",
                seq=0,
                role="assistant",
                content="doomed",
                run_id="run-doomed",
            )
        )
        session.commit()

    # 删运行：消息比运行活得久，run_id 置空。
    with Session(engine) as session:
        session.delete(session.get(models.AgentRun, "run-kept"))
        session.commit()

    with Session(engine) as session:
        kept_message = session.scalars(
            select(models.AgentMessage).where(
                models.AgentMessage.session_id == "session-kept"
            )
        ).one()
        assert kept_message.content == "kept"
        assert kept_message.run_id is None

    # 删会话：运行随会话级联删除。
    with Session(engine) as session:
        session.delete(session.get(models.AgentSession, "session-doomed"))
        session.commit()

    with Session(engine) as session:
        assert session.get(models.AgentRun, "run-doomed") is None
        assert session.get(models.AgentSession, "session-doomed") is None


# ---------------------------------------------------------------------------
# 部分唯一索引与 CHECK 的语义
# ---------------------------------------------------------------------------
def test_partial_unique_index_blocks_concurrent_updates(engine):
    """同一 target 同时只允许一条 running；终态历史可以堆积（docs/04 §5.11）。"""
    with Session(engine) as session:
        session.add(
            models.UpdateRecord(target="core", status="running", triggered_by="manual")
        )
        session.commit()

    with Session(engine) as session:
        session.add(
            models.UpdateRecord(target="core", status="running", triggered_by="manual")
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        session.add(models.UpdateRecord(target="core", status="success", triggered_by="manual"))
        session.add(models.UpdateRecord(target="core", status="failed", triggered_by="manual"))
        session.add(models.UpdateRecord(target="game", status="running", triggered_by="agent"))
        session.commit()

    with Session(engine) as session:
        rows = session.scalars(select(models.UpdateRecord)).all()
        assert len(rows) == 4
        assert sum(1 for r in rows if r.status == "running") == 2  # 不同 target 可并行


def test_resource_asset_requires_content_or_path(engine, conn):
    with Session(engine) as session:
        session.add(models.ResourceAsset(kind="custom_task", name="empty"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        session.add(
            models.ResourceAsset(kind="custom_task", name="inline", content={"a": 1})
        )
        session.add(
            models.ResourceAsset(kind="custom_task", name="on-disk", path="copilot/x.json")
        )
        session.commit()
        assert session.scalars(select(models.ResourceAsset)).all()

    assert "CHECK" in ddl(conn, "resource_asset")


# ---------------------------------------------------------------------------
# 列类型与 JSON 写法（docs/04 §3.3、§7）
# ---------------------------------------------------------------------------
def test_no_sa_enum_columns():
    for table in MD.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, Enum), (table.name, column.name)


# 按表的列宽例外：docs/v3/03 §3.2 给 ``agent_run.finish_reason`` 定的是 32，
# §4.2 给 ``cost_ledger.role`` 定的是 24；M81 规格 §4.1 给事件来源定的是 24，
# 其余表的同名列仍按上面的通用宽度。
COLUMN_WIDTH_OVERRIDES: dict[tuple[str, str], int] = {
    ("agent_run", "finish_reason"): 32,
    ("cost_ledger", "role"): 24,
    ("agent_trigger", "source"): 24,
    ("trigger_rule", "source"): 24,
    # M85 规格 §4.1：发件箱的 target 是外部 chat id / 订阅标识，定宽 255。
    ("outbox", "target"): 255,
}


@pytest.mark.parametrize("table", sorted(BUSINESS_TABLES))
def test_string_columns_declare_documented_widths(table):
    """状态/来源/级别 16、阶段/角色 24、错误码 48 的列宽是给 Alembic 的文档（§3.3）。"""
    widths = {
        "status": 16,
        "source": 16,
        "level": 16,
        "risk_level": 16,
        "target": 16,
        "type": 16,
        "caller": 16,
        "trigger": 16,
        "backend": 16,
        "role": 16,
        "phase": 24,
        "finish_reason": 24,
        "error_code": 48,
    }
    for name, width in widths.items():
        if name not in MD.tables[table].c:
            continue
        column = MD.tables[table].c[name]
        if not isinstance(column.type, sa.String):
            # 同名列不一定是字符串：`account_operator.level` 是干员等级（INTEGER），
            # 这条文档性约束只覆盖状态/来源/阶段这类字符串列。
            continue
        width = COLUMN_WIDTH_OVERRIDES.get((table, name), width)
        assert sql_type(column) == f"VARCHAR({width})", (
            table,
            name,
            sql_type(column),
        )


def test_text_columns_use_text_type():
    actual = {
        (table.name, column.name)
        for table in MD.tables.values()
        for column in table.columns
        if sql_type(column) == "TEXT"
    }
    assert actual == EXPECTED_TEXT_COLUMNS


def test_json_columns_use_json_type():
    actual = {
        (table.name, column.name)
        for table in MD.tables.values()
        for column in table.columns
        if isinstance(column.type, JSON)
    }
    assert actual == EXPECTED_JSON_COLUMNS


def test_json_columns_roundtrip_whole_assignment(engine):
    """JSON 列整体赋值后能原样读回（§7：不做原地修改）。"""
    with Session(engine) as session:
        pipeline = make_pipeline(session)
        task = models.Task(
            pipeline_id=pipeline.id,
            order_index=0,
            type_name="Fight",
            task_name="刷理智",
            params={"stage": "1-7", "medicine": 0},
            raw_params={"stage": "1-7"},
        )
        session.add(task)
        session.commit()
        task_id = task.id

    with Session(engine) as session:
        loaded = session.get(models.Task, task_id)
        assert loaded.params == {"stage": "1-7", "medicine": 0}
        assert loaded.raw_params == {"stage": "1-7"}
        loaded.params = {**loaded.params, "times": 3}
        session.add(loaded)
        session.commit()

    with Session(engine) as session:
        assert session.get(models.Task, task_id).params["times"] == 3


def test_nullable_json_none_is_sql_null_not_json_literal(engine, conn):
    """可空 JSON 列的 Python None 必须落成 SQL NULL。

    SQLAlchemy 的 ``JSON`` 默认 ``none_as_null=False``，会把 None 序列化成
    JSON 字面量 ``'null'`` 字符串 —— 那样 ``IS NULL`` 查不到，``resource_asset``
    的 ``content IS NOT NULL OR path IS NOT NULL`` 也会被绕过（M2-03 实测）。
    """
    with Session(engine) as session:
        session.add(
            models.ResourceAsset(kind="custom_task", name="on-disk", path="a/b.json")
        )
        session.add(models.LogEntry(source="server", level="info", content="x"))
        session.commit()

    assert conn.execute(
        "select content, meta from resource_asset where name = 'on-disk'"
    ).fetchone() == (None, None)
    assert (
        conn.execute("select meta from log_entry where content = 'x'").fetchone()[0]
        is None
    )
    assert conn.execute("select count(*) from resource_asset where content is null").fetchone()[0] == 1

    with Session(engine) as session:
        loaded = session.scalars(select(models.ResourceAsset)).one()
        assert loaded.content is None and loaded.meta is None


def test_setting_json_preserves_scalar_types(engine):
    with Session(engine) as session:
        session.add(models.Setting(key="adb.screenshot_quality", value=25))
        session.add(models.Setting(key="adb.address", value="127.0.0.1:5555"))
        session.commit()

    with Session(engine) as session:
        assert session.get(models.Setting, "adb.screenshot_quality").value == 25
        assert session.get(models.Setting, "adb.address").value == "127.0.0.1:5555"


def test_task_params_has_sql_server_default(conn, engine):
    """params 是 NOT NULL JSON，DDL 层带 DEFAULT '{}'（docs/04 §7 示例）。"""
    with Session(engine) as session:
        pipeline = make_pipeline(session)
        pipeline_id = pipeline.id
    conn.execute(
        "insert into task (id, pipeline_id, order_index, type_name, task_name, status,"
        " retry_count, max_retries, retry_delay, created_at)"
        " values (?,?,?,?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            pipeline_id,
            0,
            "StartUp",
            "开始唤醒",
            "pending",
            0,
            3,
            30,
            "2026-01-01 00:00:00",
        ),
    )
    conn.commit()
    params = conn.execute("select params from task").fetchone()[0]
    assert params == "{}"


# ---------------------------------------------------------------------------
# 默认值与时间戳
# ---------------------------------------------------------------------------
def test_utcnow_is_naive_utc():
    before = datetime.now(UTC).replace(tzinfo=None)
    value = utcnow()
    after = datetime.now(UTC).replace(tzinfo=None)
    assert value.tzinfo is None
    assert before - timedelta(seconds=1) <= value <= after + timedelta(seconds=1)


def test_pipeline_orm_defaults(engine):
    with Session(engine) as session:
        pipeline = models.Pipeline(source="scheduled", priority=2, task_count=3)
        session.add(pipeline)
        session.commit()
        assert pipeline.status == "pending"
        assert pipeline.core_id == "default"
        assert pipeline.notify_on_finish is True
        assert pipeline.core_epoch is None
        assert pipeline.started_at is None
        assert pipeline.finished_at is None
        assert pipeline.created_at.tzinfo is None
        assert (
            datetime.now(UTC).replace(tzinfo=None) - pipeline.created_at
        ).total_seconds() < 10


def test_schedule_and_agent_session_defaults(engine):
    with Session(engine) as session:
        schedule = models.Schedule(name="每日常规", cron="0 7,19 * * *", template=[])
        session.add(schedule)
        agent_session = models.AgentSession(model="gpt-test")
        session.add(agent_session)
        session.commit()
        assert schedule.timezone == "Asia/Shanghai"
        assert schedule.enabled is True
        assert schedule.priority == 2
        assert schedule.misfire_grace_seconds == 300
        assert schedule.catch_up is False
        assert schedule.skip_if_running is True
        assert schedule.updated_at.tzinfo is None
        assert agent_session.status == "active"
        assert agent_session.message_count == 0
        assert agent_session.prompt_tokens == 0
        assert agent_session.completion_tokens == 0
        assert agent_session.atomic_grant_id is None
        assert agent_session.last_message_at is None


def test_agent_run_orm_defaults(engine):
    """只写必填列：status=running、五个计数为 0、started_at 是 naive UTC（M66 §4.1.2）。"""
    with Session(engine) as session:
        session.add(
            models.AgentRun(
                root_run_id="root-run",
                agent_name="legacy_chat",
                kind="legacy_turn",
                boot_id="boot-1",
            )
        )
        session.commit()

    with Session(engine) as session:
        row = session.scalars(select(models.AgentRun)).one()
        assert row.status == "running"
        assert row.prompt_tokens == 0
        assert row.completion_tokens == 0
        assert row.cached_tokens == 0
        assert row.reasoning_tokens == 0
        assert row.cost_micros == 0
        assert row.steps == 0
        assert row.finished_at is None
        assert row.heartbeat_at is None
        assert row.session_id is None
        assert row.parent_run_id is None
        assert row.meta is None
        assert row.started_at.tzinfo is None
        assert (
            datetime.now(UTC).replace(tzinfo=None) - row.started_at
        ).total_seconds() < 10


def test_agent_knowledge_orm_defaults(engine):
    """覆盖行可以只写一列；``enabled`` 非空、默认真（M18 规格 §4.1）。"""
    with Session(engine) as session:
        row = models.AgentKnowledge(id="screen-home")
        session.add(row)
        session.commit()
        assert row.kind is None
        assert row.title is None
        assert row.when_to_use is None
        assert row.body is None
        assert row.enabled is True
        assert row.created_at.tzinfo is None
        assert row.updated_at.tzinfo is None


def test_screenshot_trigger_column_is_quoted_in_ddl(conn):
    """``trigger`` 是 SQLite 保留字，必须被引用；否则建表就炸。"""
    assert '"TRIGGER" VARCHAR(16) NOT NULL' in ddl(conn, "screenshot")


# ---------------------------------------------------------------------------
# M67：模型档案与成本账本（v3 docs/03 §4.1、§4.2）
# ---------------------------------------------------------------------------
def test_model_profile_name_is_unique(engine):
    """同名档案由 ``uq_model_profile_name`` 兜底（``default`` 不入表，name 仍唯一）。"""
    with Session(engine) as session:
        session.add(models.ModelProfile(name="main", model="gpt-test"))
        session.commit()

    with Session(engine) as session:
        session.add(models.ModelProfile(name="main", model="other-model"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        rows = session.scalars(select(models.ModelProfile)).all()
        assert [row.model for row in rows] == ["gpt-test"]


def test_cost_ledger_autoincrement_and_fk_set_null(engine):
    """账本自增且尾部删除后 id 不复用；删运行/会话只把两个外键置空。"""
    with Session(engine) as session:
        session.add(models.AgentSession(id="session-cost", model="gpt-test"))
        session.commit()

    with Session(engine) as session:
        session.add(
            models.AgentRun(
                id="run-cost",
                session_id="session-cost",
                root_run_id="run-cost",
                agent_name="legacy_chat",
                kind="legacy_turn",
                boot_id="boot-1",
            )
        )
        session.commit()

    with Session(engine) as session:
        first = models.CostLedgerEntry(
            run_id="run-cost",
            session_id="session-cost",
            agent_name="legacy_chat",
            role="worker",
            model_profile="main",
            model="gpt-test",
            currency="CNY",
        )
        session.add(first)
        session.commit()
        first_id = first.id

        second = models.CostLedgerEntry(
            agent_name="legacy_chat",
            role="worker",
            model_profile="main",
            model="gpt-test",
            currency="CNY",
        )
        session.add(second)
        session.commit()
        second_id = second.id
        session.delete(second)
        session.commit()

    assert first_id is not None and second_id is not None
    assert second_id > first_id

    # 删掉尾部行后继续追加：AUTOINCREMENT 让新 id 严格大于被删掉的 id；
    # 只有 INTEGER PRIMARY KEY 时 SQLite 会取 max(rowid)+1，把 2 复用掉。
    with Session(engine) as session:
        third = models.CostLedgerEntry(
            agent_name="legacy_chat",
            role="worker",
            model_profile="main",
            model="gpt-test",
            currency="CNY",
        )
        session.add(third)
        session.commit()
        assert third.id > second_id

    # 删运行：账本行还在，run_id 置空。
    with Session(engine) as session:
        session.delete(session.get(models.AgentRun, "run-cost"))
        session.commit()

    with Session(engine) as session:
        row = session.get(models.CostLedgerEntry, first_id)
        assert row is not None
        assert row.run_id is None
        assert row.session_id == "session-cost"

    # 删会话：session_id 也置空，账本行比主体活得久。
    with Session(engine) as session:
        session.delete(session.get(models.AgentSession, "session-cost"))
        session.commit()

    with Session(engine) as session:
        row = session.get(models.CostLedgerEntry, first_id)
        assert row is not None
        assert row.session_id is None
