"""M89 T5：自定义任务起草与校验工具（spec §4.7、§5 T5、§6）。

覆盖：

- ``draft_custom_task`` 只调编译器：返回 ``entry/tasks/chain/warnings``，长按步骤带
  「长按编译成起终点相同的滑动」warning，不读服务、不写库、不写文件；
- ``validate_custom_task`` 先逐节点结构校验、再链级校验，返回
  ``valid/errors/chain_errors/chain``，校验失败不抛 ``AppError``；
- ``register_custom_task`` 的 ``content`` / ``tasks`` 恰好一个，``tasks`` 原样交给服务。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from maa_api.agent.demonstrations.compiler import Step, compile_steps
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.resource_service import ResourceService

_STEP_SCREEN = [1280, 720]


def _context(state: Any = None) -> ToolContext:
    return ToolContext(
        caller=CallerType.REST,
        session_id="agent-session",
        request_id="custom-task-tools",
        request=SimpleNamespace(app=SimpleNamespace(state=state or SimpleNamespace())),
        db_session=None,
    )


def _run(name: str, arguments: dict[str, Any], context: ToolContext) -> Any:
    return asyncio.run(build_registry().execute(name, arguments, context))


class _Resources:
    """资源服务替身：校验可注入错误，注册只记录调用。"""

    def __init__(self, *, content_errors: list[dict[str, str]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._content_errors = content_errors or []

    def validate_custom_task_content(self, content: Any) -> list[dict[str, str]]:
        del content
        return [dict(item) for item in self._content_errors]

    async def register_custom_task(
        self,
        *,
        name: str,
        description: str | None,
        content: dict[str, Any] | None = None,
        tasks: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "name": name,
                "description": description,
                "content": content,
                "tasks": tasks,
            }
        )
        names = list(tasks) if tasks is not None else [name]
        return {
            "entry": name,
            "task_names": names,
            "items": [{"id": f"asset-{index}", "name": item} for index, item in enumerate(names)],
        }


def test_draft_returns_bundle_without_writing(tmp_path) -> None:
    """只调 ``compile_steps``：返回四个键，不碰资源服务，也不落任何文件。"""
    context = _context()
    steps = [
        {"action": "click", "x": 640, "y": 360, "screen": _STEP_SCREEN},
        {
            "action": "long_press",
            "x": 100,
            "y": 200,
            "duration_ms": 800,
            "delay_ms": 300,
            "screen": _STEP_SCREEN,
        },
    ]

    result = _run(
        "draft_custom_task",
        {"stage_code": "act52side_ex08", "steps": steps},
        context,
    )

    assert set(result) == {"entry", "tasks", "chain", "warnings"}
    assert result["entry"] == "Custom_act52side_ex08_1"
    assert result["warnings"] == ["长按编译成起终点相同的滑动"]
    # chain 就是节点顺序，最后一个节点是 Stop 出口。
    assert list(result["tasks"]) == result["chain"]
    assert result["tasks"][result["chain"][-1]] == {
        "algorithm": "JustReturn",
        "action": "Stop",
    }
    # 与编译器逐字一致：起草不做第二次加工。
    expected = compile_steps(
        prefix="Custom_act52side_ex08",
        steps=[
            Step(action="click", x=640, y=360, screen=(1280, 720)),
            Step(
                action="long_press",
                x=100,
                y=200,
                duration_ms=800,
                delay_ms=300,
                screen=(1280, 720),
            ),
        ],
    )
    assert result["tasks"] == expected.tasks

    # 给了 name 就用 name；没有长按就没有 warning。
    named = _run(
        "draft_custom_task",
        {
            "stage_code": "act52side_ex08",
            "name": "Custom_MyRun",
            "steps": [{"action": "click", "x": 1, "y": 2, "screen": _STEP_SCREEN}],
        },
        context,
    )
    assert named["entry"] == "Custom_MyRun_1"
    assert named["warnings"] == []


def test_validate_and_register(tmp_path) -> None:
    """校验不抛错且错误数组完整；注册一次且 name 是入口；二选一参数校验失败。"""
    # 真正的结构校验包装：ResourceService 的构造不外连，校验方法纯计算。
    validator = ResourceService(SimpleNamespace(), resource_root=tmp_path)
    validate_context = _context(SimpleNamespace(resource_service=validator))

    broken = {
        "Custom_Bad_1": {
            "algorithm": "JustReturn",
            "action": "InputText",
            "boom": 1,
            "next": ["Custom_Bad_Done"],
        },
        "Custom_Bad_Done": {"algorithm": "JustReturn", "action": "DoNothing"},
    }

    invalid = _run(
        "validate_custom_task",
        {"entry": "Custom_Bad_1", "tasks": broken},
        validate_context,
    )

    assert invalid["valid"] is False
    assert invalid["chain"] == []
    # 逐节点结构校验的错误：未知字段由 v2 校验器报出，工具补上节点名。
    assert any(
        item.get("task") == "Custom_Bad_1" and item.get("field") == "content"
        for item in invalid["errors"]
    )
    # 链级校验的错误：action 白名单与「没有 Stop 出口」都在。
    assert any(item.get("field") == "action" for item in invalid["errors"])
    assert any("Stop" in item.get("reason", "") for item in invalid["chain_errors"])

    # 用起草结果校验：valid=true，四个键齐全。
    resources = _Resources()
    context = _context(SimpleNamespace(resource_service=resources))
    draft = _run(
        "draft_custom_task",
        {
            "stage_code": "act52side_ex08",
            "steps": [{"action": "click", "x": 640, "y": 360, "screen": _STEP_SCREEN}],
        },
        context,
    )
    valid = _run(
        "validate_custom_task",
        {"entry": draft["entry"], "tasks": draft["tasks"]},
        context,
    )
    assert valid == {
        "valid": True,
        "errors": [],
        "chain_errors": [],
        "chain": draft["chain"],
    }

    # 批量注册：tasks 原样交给服务，name 是入口。
    registered = _run(
        "register_custom_task",
        {"name": draft["entry"], "tasks": draft["tasks"]},
        context,
    )
    assert len(resources.calls) == 1
    assert resources.calls[0] == {
        "name": draft["entry"],
        "description": None,
        "content": None,
        "tasks": draft["tasks"],
    }
    assert registered["entry"] == draft["entry"]

    # 旧 content 调用不变：content 原样透传，tasks 为 None。
    _run(
        "register_custom_task",
        {"name": "Custom_Old", "content": {"action": "Stop"}},
        context,
    )
    assert resources.calls[-1]["content"] == {"action": "Stop"}
    assert resources.calls[-1]["tasks"] is None

    # 两个都给或都不给都是参数错误；tasks 超过工具层上限 32 也是参数错误。
    registry = build_registry()
    for arguments in (
        {"name": "Custom_X", "content": {"action": "Stop"}, "tasks": draft["tasks"]},
        {"name": "Custom_X"},
    ):
        with pytest.raises(AppError) as caught:
            registry.validate("register_custom_task", arguments)
        assert caught.value.code == ErrorCode.TOOL_ARGS_INVALID
    too_many = {
        f"Custom_X_{index}": {"algorithm": "JustReturn", "action": "Stop"}
        for index in range(33)
    }
    with pytest.raises(AppError) as caught:
        registry.validate(
            "register_custom_task",
            {"name": "Custom_X_0", "tasks": too_many},
        )
    assert caught.value.code == ErrorCode.TOOL_ARGS_INVALID
