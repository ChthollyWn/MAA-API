"""``maa_api/domain/enums.py`` 的契约测试（docs/04 §4）。

三件事：取值快照与文档逐字一致、``StrEnum`` 语义成立（可直接进 JSON/响应体）、
``PipelineStatus.is_terminal`` 状态机正确。
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
    "RiskLevel": {
        "NONE": "none",
        "CONSUME": "consume",
        "DESTRUCTIVE": "destructive",
    },
    "CallerType": {
        "REST": "rest",
        "MCP": "mcp",
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
        "STOPPING_CORE": "stopping_core",
        "EXTRACTING": "extracting",
        "INSTALLING": "installing",
        "STARTING_CORE": "starting_core",
        "DONE": "done",
    },
    "NotifyChannelType": {
        "EMAIL": "email",
        "WEBHOOK": "webhook",
        "BARK": "bark",
        "DINGTALK": "dingtalk",
        "WECOM": "wecom",
    },
    "NotifyEvent": {
        "PIPELINE_COMPLETED": "pipeline_completed",
        "PIPELINE_FAILED": "pipeline_failed",
        "CORE_CRASHED": "core_crashed",
        "DEVICE_DISCONNECTED": "device_disconnected",
        "UPDATE_FINISHED": "update_finished",
        "CONFIRMATION_REQUIRED": "confirmation_required",
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
    "ResourceAssetKind": {
        "COPILOT": "copilot",
        "INFRAST_PLAN": "infrast_plan",
        "CUSTOM_TASK": "custom_task",
        "OTA_RESOURCE": "ota_resource",
        "REPO_RESOURCE": "repo_resource",
    },
}

# 运行期内存状态，只在日志与响应体里以字符串出现（docs/04 §4）。
NON_PERSISTED = ("CoreStatus", "DeviceStatus")

# 内核指令枚举归 maa_api/core/enums.py，不在 domain 里重复。
CORE_ONLY = ("Message", "InstanceOptionKey", "StaticOptionKey")

TERMINAL_PIPELINE_STATUSES = {"completed", "failed", "cancelled"}


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
