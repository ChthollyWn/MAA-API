"""``maa_api/domain/enums.py`` 的契约测试（docs/04 §4）。

三件事：取值快照与文档逐字一致、``StrEnum`` 语义成立（可直接进 JSON/响应体）、
``PipelineStatus`` / ``TicketStatus`` 的 ``is_terminal`` 状态机正确。
"""

import enum
import json

import pytest

from maa_api.domain import enums

# docs/04 §4 代码块的逐字快照：{枚举名: {成员名: 取值}}。
EXPECTED_VALUES = {
    "PipelineStatus": {
        "PENDING": "pending",
        "RUNNING": "running",
        "COMPLETED": "completed",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
    },
    "TaskStatus": {
        "PENDING": "pending",
        "RUNNING": "running",
        "COMPLETED": "completed",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
        "SKIPPED": "skipped",
    },
    "PipelineSource": {
        "MANUAL": "manual",
        "AGENT": "agent",
        "SCHEDULED": "scheduled",
    },
    "Priority": {
        "MANUAL": 0,
        "AGENT": 1,
        "SCHEDULED": 2,
    },
    "LogSource": {
        "MAA_TASK": "maa_task",
        "SERVER": "server",
        "MAACORE_DEBUG": "maacore_debug",
    },
    "LogLevel": {
        "DEBUG": "debug",
        "INFO": "info",
        "WARNING": "warning",
        "ERROR": "error",
        "CRITICAL": "critical",
    },
    "ScreenshotTrigger": {
        "TASK_SNAPSHOT": "task_snapshot",
        "MANUAL": "manual",
        "AGENT": "agent",
        "CRASH": "crash",
        "UPDATE": "update",
    },
    "ScreenshotBackend": {
        "CORE": "core",
        "ADB": "adb",
    },
    "ConfirmationStatus": {
        "PENDING": "pending",
        "APPROVED": "approved",
        "REJECTED": "rejected",
        "EXPIRED": "expired",
    },
    "QuestionStatus": {
        "PENDING": "pending",
        "ANSWERED": "answered",
        "SKIPPED": "skipped",
        "EXPIRED": "expired",
        "CANCELLED": "cancelled",
    },
    "RiskLevel": {
        "NONE": "none",
        "CONSUME": "consume",
        "DESTRUCTIVE": "destructive",
    },
    "CallerType": {
        "REST": "rest",
        "INTERNAL": "internal",
    },
    "AuditStatus": {
        "PENDING": "pending",
        "SUCCESS": "success",
        "FAILED": "failed",
        "REJECTED": "rejected",
        "EXPIRED": "expired",
    },
    "UpdateTarget": {
        "CORE": "core",
        "RESOURCE": "resource",
        "GAME": "game",
    },
    "UpdateStatus": {
        "PENDING": "pending",
        "RUNNING": "running",
        "SUCCESS": "success",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
        "SKIPPED": "skipped",
    },
    "ReleaseChannel": {
        "STABLE": "stable",
        "BETA": "beta",
        "ALPHA": "alpha",
    },
    "ResourceChannel": {
        "OTA": "ota",
        "REPO": "repo",
        "ALL": "all",
    },
    "UpdatePhase": {
        "CHECKING": "checking",
        "DOWNLOADING": "downloading",
        "VERIFYING": "verifying",
        "WAITING_IDLE": "waiting_idle",
        "APPLYING": "applying",
        "RESTARTING": "restarting",
        "DONE": "done",
        "FAILED": "failed",
    },
    "NotifyChannelType": {
        "EMAIL": "email",
        "WEBHOOK": "webhook",
        "BARK": "bark",
        "DINGTALK": "dingtalk",
        "WECOM": "wecom",
        "FEISHU": "feishu",
    },
    "NotifyEvent": {
        "PIPELINE_COMPLETED": "pipeline_completed",
        "PIPELINE_FAILED": "pipeline_failed",
        "CORE_CRASHED": "core_crashed",
        "DEVICE_DISCONNECTED": "device_disconnected",
        "UPDATE_AVAILABLE": "update_available",
        "UPDATE_FINISHED": "update_finished",
        "CONFIRMATION_REQUIRED": "confirmation_required",
        # M27：养成计划的材料备齐、定时任务已停（S15）。
        "UPGRADE_PLAN_FINISHED": "upgrade_plan_finished",
        # M29：作业研究的授权进入终态（规格 §9.9），渠道与浏览器推送各发一次。
        "COPILOT_RESEARCH_FINISHED": "copilot_research_finished",
    },
    "AgentRole": {
        "SYSTEM": "system",
        "USER": "user",
        "ASSISTANT": "assistant",
        "TOOL": "tool",
    },
    "AgentSessionStatus": {
        "ACTIVE": "active",
        "FINISHED": "finished",
        "ERROR": "error",
    },
    "AgentSessionKind": {
        "CHAT": "chat",
        "INCIDENT": "incident",
        "ORCHESTRATOR": "orchestrator",
        "WORKER": "worker",
        "SUBAGENT": "subagent",
        "BACKGROUND": "background",
    },
    "AgentRunKind": {
        "ORCHESTRATOR_TURN": "orchestrator_turn",
        "WORKER_TURN": "worker_turn",
        "SUBAGENT": "subagent",
        "TRIGGER_EVAL": "trigger_eval",
        "COMPACTION": "compaction",
        "MEMORY_EXTRACT": "memory_extract",
        "TITLE": "title",
        "WATCHDOG_CHECK": "watchdog_check",
        "EVAL_CASE": "eval_case",
        "LEGACY_TURN": "legacy_turn",
    },
    "AgentRunStatus": {
        "RUNNING": "running",
        "WAITING_HUMAN": "waiting_human",
        "SUCCEEDED": "succeeded",
        "FAILED": "failed",
        "INTERRUPTED": "interrupted",
        "CANCELLED": "cancelled",
    },
    "ResourceAssetKind": {
        "COPILOT": "copilot",
        "INFRAST_PLAN": "infrast_plan",
        "CUSTOM_TASK": "custom_task",
        "OTA_RESOURCE": "ota_resource",
        "REPO_RESOURCE": "repo_resource",
    },
    # M67 模型层（docs/03 §4.1、§13）。
    "ModelProfileKind": {
        "CHAT": "chat",
        "EMBEDDING": "embedding",
    },
    "ModelProvider": {
        "OPENAI_COMPATIBLE": "openai_compatible",
        "LOCAL_ONNX": "local_onnx",
    },
    "ModelRole": {
        "ORCHESTRATOR": "orchestrator",
        "WORKER": "worker",
        "SUBAGENT": "subagent",
        "SUMMARIZER": "summarizer",
        "TITLE": "title",
        "TRIGGER_EVAL": "trigger_eval",
        "MEMORY": "memory",
        "VISION": "vision",
        "EMBEDDING": "embedding",
    },
    "DynamicContextPosition": {
        "TAIL_SYSTEM": "tail_system",
        "TAIL_USER": "tail_user",
    },
    # M68 运行追踪（v3 docs/03 §5.1、10 §2.2）。
    "SpanKind": {
        "LLM": "llm",
        "TOOL": "tool",
        "RETRIEVAL": "retrieval",
        "POLICY": "policy",
        "CONFIRMATION": "confirmation",
        "MEMORY": "memory",
        "COMPACTION": "compaction",
        "LEASE": "lease",
        "DELEGATE": "delegate",
        "OTHER": "other",
    },
    # M69 工单领域（v3 specs/m69 §4.1）。
    "TicketStatus": {
        "OPEN": "open",
        "WAITING": "waiting",
        "BLOCKED": "blocked",
        "DONE": "done",
        "FAILED": "failed",
        "CANCELLED": "cancelled",
    },
    "TicketOrigin": {
        "USER": "user",
        "TRIGGER": "trigger",
        "GOAL": "goal",
        "WATCHDOG": "watchdog",
        "SYSTEM": "system",
    },
    "MandateStrength": {
        "EXPLICIT": "explicit",
        "INFERRED": "inferred",
    },
    "EventSource": {
        "WEB": "web",
        "IM_TELEGRAM": "im_telegram",
        "IM_FEISHU": "im_feishu",
        "IM_DINGTALK": "im_dingtalk",
        "IM_QQ": "im_qq",
        "PIPELINE": "pipeline",
        "CORE": "core",
        "DEVICE": "device",
        "SCHEDULE": "schedule",
        "SYSTEM": "system",
        "WATCHDOG": "watchdog",
        "GOAL": "goal",
    },
    "ReportStatus": {
        "PROGRESS": "progress",
        "WAITING": "waiting",
        "READY": "ready",
        "BLOCKED": "blocked",
        "FAILED": "failed",
        "DROPPED": "dropped",
    },
    "InboxKind": {
        "USER_MESSAGE": "user_message",
        "REPORT": "report",
        "THREAD_EVENT": "thread_event",
        "SYSTEM": "system",
        "TRIGGER": "trigger",
    },
    "InboxStatus": {
        "PENDING": "pending",
        "CONSUMED": "consumed",
        "DROPPED": "dropped",
    },
    # M74：压缩检查点的触发方式与状态（v3 specs/m74 §4.1）。
    "CompactionTrigger": {
        "AUTO": "auto",
        "MANUAL": "manual",
    },
    "CheckpointStatus": {
        "ACTIVE": "active",
        "SUPERSEDED": "superseded",
        "INVALIDATED": "invalidated",
    },
    # M75：产物与计划（v3 specs/m75 §4.1）。
    "ArtifactKind": {
        "NOTE": "note",
        "SPILL": "spill",
        "REPORT": "report",
        "CHART": "chart",
        "TABLE": "table",
        "FILE": "file",
        "FORM": "form",
        "DASHBOARD": "dashboard",
    },
    "ArtifactStorage": {
        "DB": "db",
        "FILE": "file",
    },
    "PlanStatus": {
        "DRAFT": "draft",
        "AWAITING_APPROVAL": "awaiting_approval",
        "ACTIVE": "active",
        "DONE": "done",
        "ABANDONED": "abandoned",
    },
    "PlanItemStatus": {
        "PENDING": "pending",
        "IN_PROGRESS": "in_progress",
        "DONE": "done",
        "CANCELLED": "cancelled",
    },
    # M76：检索来源的种类（v3 specs/m76 §4.1、docs/v3/03 §13）。
    "KnowledgeSourceKind": {
        "BUILTIN": "builtin",
        "USER": "user",
        "MAA_DOCS": "maa_docs",
        "PRTS": "prts",
        "GAMEDATA": "gamedata",
        "COPILOT_SITE": "copilot_site",
        "HISTORY": "history",
        "MEMORY": "memory",
    },
    # M78：技能行的来历（v3 specs/m78 §4.1）。
    "SkillOrigin": {
        "OVERRIDE": "override",
        "USER": "user",
        "GENERATED": "generated",
    },
}

# 运行期内存状态，只在日志与响应体里以字符串出现（docs/04 §4）。
NON_PERSISTED = ("CoreStatus", "DeviceStatus")

# 内核指令枚举归 maa_api/core/enums.py，不在 domain 里重复。
CORE_ONLY = ("Message", "InstanceOptionKey", "StaticOptionKey")

TERMINAL_PIPELINE_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_TICKET_STATUSES = {"done", "failed", "cancelled"}


@pytest.mark.parametrize("name", sorted(EXPECTED_VALUES))
def test_enum_values_match_docs(name):
    cls = getattr(enums, name)
    assert {member.name: member.value for member in cls} == EXPECTED_VALUES[name]


def test_all_persisted_enums_are_defined_and_nothing_else():
    defined = {
        name
        for name, obj in vars(enums).items()
        if isinstance(obj, type)
        and issubclass(obj, enum.Enum)
        and obj.__module__ == enums.__name__
    }
    assert defined == set(EXPECTED_VALUES)
    assert not defined & set(NON_PERSISTED)
    for extra in NON_PERSISTED + CORE_ONLY:
        assert not hasattr(enums, extra), f"{extra} 不该出现在 domain/enums.py"


@pytest.mark.parametrize("name", sorted(EXPECTED_VALUES))
def test_every_member_is_string_backed_except_priority(name):
    cls = getattr(enums, name)
    if name == "Priority":
        assert issubclass(cls, enum.IntEnum)
        assert not issubclass(cls, str)
        return
    assert issubclass(cls, enum.StrEnum)
    for member in cls:
        assert isinstance(member, str)
        assert member == member.value
        assert str(member) == member.value
        assert json.dumps({"v": member}) == json.dumps({"v": member.value})


def test_pipeline_status_is_terminal():
    for member in enums.PipelineStatus:
        assert member.is_terminal is (member.value in TERMINAL_PIPELINE_STATUSES)
    assert enums.PipelineStatus.PENDING.is_terminal is False
    assert enums.PipelineStatus.RUNNING.is_terminal is False


def test_ticket_status_is_terminal():
    """只有 done、failed、cancelled 是终态（M69 规格 §4.1）。"""
    for member in enums.TicketStatus:
        assert member.is_terminal is (member.value in TERMINAL_TICKET_STATUSES)
    assert enums.TicketStatus.OPEN.is_terminal is False
    assert enums.TicketStatus.WAITING.is_terminal is False
    assert enums.TicketStatus.BLOCKED.is_terminal is False


def test_status_enum_alignment_and_renames():
    # IDLE 改名 PENDING，与 TaskStatus.PENDING 对齐（docs/04 §4）。
    assert not hasattr(enums.PipelineStatus, "IDLE")
    assert enums.PipelineStatus.PENDING == enums.TaskStatus.PENDING
    # SKIPPED 填补「重试耗尽后跳过、流水线继续」的表达空白。
    assert enums.TaskStatus.SKIPPED not in {
        enums.TaskStatus.COMPLETED,
        enums.TaskStatus.FAILED,
        enums.TaskStatus.CANCELLED,
    }
    assert enums.Priority.MANUAL < enums.Priority.AGENT < enums.Priority.SCHEDULED
    assert enums.Priority(0) is enums.Priority.MANUAL
