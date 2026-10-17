"""任务对象在**模型侧**的紧凑 schema（M56，docs/13 §8.79）。

为什么需要单独一份：``PipelineCreate`` / ``ScheduleWrite`` 的 Pydantic schema 会把
12 种内核任务的完整字段图（含每个字段的中文说明、取值范围与默认值）塞进每条
``tools`` 声明的 ``$defs``。三个工具各带一份完全相同的 36 KB 任务图，实测占掉
初始请求 64 497 token 里的约四成——而模型真正需要知道的只是「有哪些任务类型、字段
去 ``list_task_types`` 查」。

因此模型侧只暴露「任务对象 = 必填 ``name`` + 其余字段由任务类型决定」，执行侧仍然
用完整 Pydantic 模型校验（REST/OpenAPI 快照不受影响）。字段细节由 ``list_task_types``
按需提供，模型也只有在用新任务类型时才需要读它。
"""

from __future__ import annotations

from typing import Any

from maa_api.domain.task import TASK_LABELS

__all__ = [
    "TASK_ITEM_SCHEMA",
    "compact_task_item_schema",
    "pipeline_model_schema",
    "schedule_model_schema",
]

#: 可提交的内核任务名（按字典序，便于模型枚举时稳定）。
TASK_NAMES = sorted(TASK_LABELS)


def compact_task_item_schema() -> dict[str, Any]:
    """一个内核任务对象的模型侧 schema：``name`` 必填，其余字段开放。"""
    return {
        "type": "object",
        "additionalProperties": True,
        # 说明里点名 list_task_types：不读它直接瞎填字段时，Pydantic 的
        # ``issues`` 会报出具体字段，模型有一次自纠机会，但别鼓励走这条路。
        "description": (
            "一个 MaaCore 任务对象。必填 name；其余字段取决于任务类型，"
            "先调 list_task_types 查该类型的字段名、取值范围与默认值。"
        ),
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "enum": TASK_NAMES,
                "description": "任务类型（StartUp / Fight / Copilot / …）；枚举即全部可用类型。",
            }
        },
    }


TASK_ITEM_SCHEMA: dict[str, Any] = compact_task_item_schema()


def _tasks_array_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": 32,
        "description": "按顺序执行的任务列表；每种任务的字段先用 list_task_types 查。",
        "items": TASK_ITEM_SCHEMA,
    }


def pipeline_model_schema() -> dict[str, Any]:
    """``submit_pipeline`` 的模型侧参数 schema（与 ``PipelineCreate`` 校验一致）。"""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {
                "anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}],
                "default": None,
                "description": "流水线标题，可省略。",
            },
            "tasks": _tasks_array_schema(),
            "priority": {
                "anyOf": [
                    {"type": "integer", "minimum": 0, "maximum": 2},
                    {"type": "null"},
                ],
                "default": None,
                "description": "0 最高、2 最低；不填按入口默认。",
            },
            "notify_on_finish": {
                "type": "boolean",
                "default": True,
                "description": "结束时是否通知。",
            },
        },
        "required": ["tasks"],
    }


def _schedule_fields() -> dict[str, Any]:
    return {
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "description": "定时任务名称。",
        },
        "cron": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "description": "五段 cron 表达式（分 时 日 月 周），按 timezone 解释。",
        },
        "timezone": {
            "type": "string",
            "minLength": 1,
            "maxLength": 48,
            "default": "Asia/Shanghai",
            "description": "IANA 时区，默认 Asia/Shanghai。",
        },
        "template": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "description": "到点执行的任务列表；字段先用 list_task_types 查。",
            "items": TASK_ITEM_SCHEMA,
        },
        "enabled": {"type": "boolean", "default": True, "description": "是否启用。"},
        "priority": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2,
            "default": 2,
            "description": "0 最高、2 最低；定时任务默认 2。",
        },
        "misfire_grace_seconds": {
            "type": "integer",
            "minimum": 1,
            "maximum": 86400,
            "default": 300,
            "description": "错过触发后允许补跑的宽限秒数。",
        },
        "catch_up": {"type": "boolean", "default": False, "description": "是否补跑错过的执行。"},
        "skip_if_running": {
            "type": "boolean",
            "default": True,
            "description": "同一时间已有流水线在跑时是否跳过本次。",
        },
    }


def schedule_model_schema(*, update: bool = False) -> dict[str, Any]:
    """``create_schedule`` / ``update_schedule`` 的模型侧参数 schema。"""
    properties = _schedule_fields()
    required = ["name", "cron", "template"]
    if update:
        properties = {
            "schedule_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 36,
                "description": "要整条替换的定时任务 id。",
            },
            **properties,
        }
        required = ["schedule_id", *required]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }
