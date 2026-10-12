"""M42 §4.7：森空岛两个 Agent 工具（`get_skland_status` / `sync_skland_training`）。

四组断言：

- 元信息：状态查询是 SAFE + ``read_only``；同步是 CONDITIONAL 且写库；
- 参数边界：两个模型都 ``extra="forbid"``（空参数工具不该接受任何自造字段）；
- 派发：handler 只认 ``main.py`` 的装配名 ``skland_sync_service``——它带凭据存储与出站
  客户端，不是会话工厂能现造的，没装配必须报装配错误而不是 TypeError；
- 策略：同步**每次**要人工确认（挂 ``ACCOUNT_WRITE_TOOLS``，理由是覆盖整表练度），
  状态查询不弹卡片（挂 ``SAFE_TOOL_NAMES``）。

不打网、不碰数据库：服务是假件，塞进 ``app.state``。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext, ToolRisk
from maa_api.agent.tools import build_registry
from maa_api.domain.enums import CallerType, RiskLevel
from maa_api.domain.errors import AppError, ErrorCode

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class FakeSyncService:
    """记账用的假服务：不发出站请求，只核对调用与摘要。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def status(self) -> dict[str, Any]:
        self.calls.append("status")
        return {
            "bound": True,
            "key_configured": True,
            "account": {"uid": "1234567890", "nickname": "博士"},
            "roles": [{"uid": "1234567890", "is_default": True}],
            "last_sync_at": "2026-10-12T05:00:00Z",
            "last_sync": None,
            "last_error": None,
            "min_sync_interval_seconds": 60,
            "min_chars": 200,
            "auto_sync_enabled": False,
            "auto_sync_interval_minutes": 0,
        }

    async def sync(self) -> dict[str, Any]:
        self.calls.append("sync")
        return {
            "status": "succeeded",
            "role": {"uid": "1234567890"},
            "synced_at": "2026-10-12T05:00:00Z",
            "total": 384,
            "created": 1,
            "updated": 40,
            "unchanged": 343,
            "ownership_cleared": 0,
            "diffs": 2,
            "dropped_invalid": 0,
            "duration_ms": 620,
        }


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        "session-1",
        "request-1",
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def _definition(name: str):
    return build_registry().get(name)


async def call(name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    definition = build_registry().get(name)
    params = definition.params_model.model_validate(arguments)
    return await definition.handler(params, ctx)


# ---------------------------------------------------------------------------
# 元信息与参数边界
# ---------------------------------------------------------------------------


def test_skland_status_tool_is_safe_and_read_only() -> None:
    definition = _definition("get_skland_status")
    assert definition.group == "account"
    assert definition.risk is ToolRisk.SAFE
    assert definition.read_only is True


def test_sync_skland_training_tool_is_conditional_and_writes() -> None:
    definition = _definition("sync_skland_training")
    assert definition.group == "account"
    assert definition.risk is ToolRisk.CONDITIONAL
    assert definition.read_only is False
    assert "森空岛" in definition.description
    assert "全部" in definition.description


@pytest.mark.parametrize("name", ["get_skland_status", "sync_skland_training"])
def test_skland_tools_reject_unknown_arguments(name: str) -> None:
    definition = _definition(name)
    with pytest.raises(ValidationError):
        definition.params_model.model_validate({"mode": "full"})


# ---------------------------------------------------------------------------
# 派发
# ---------------------------------------------------------------------------


def test_status_handler_dispatches_to_the_assembled_service() -> None:
    service = FakeSyncService()
    result = asyncio.run(call("get_skland_status", {}, context(skland_sync_service=service)))
    assert service.calls == ["status"]
    assert result["bound"] is True


def test_sync_handler_dispatches_to_the_assembled_service() -> None:
    service = FakeSyncService()
    result = asyncio.run(call("sync_skland_training", {}, context(skland_sync_service=service)))
    assert service.calls == ["sync"]
    assert result["total"] == 384


@pytest.mark.parametrize("name", ["get_skland_status", "sync_skland_training"])
def test_skland_tools_require_assembly(name: str) -> None:
    with pytest.raises(AppError) as error:
        asyncio.run(call(name, {}, context()))
    assert error.value.code is ErrorCode.INTERNAL_ERROR
    assert "skland_sync_service" in error.value.message


# ---------------------------------------------------------------------------
# 策略
# ---------------------------------------------------------------------------


def test_sync_requires_confirmation_every_time() -> None:
    engine = PolicyEngine()
    decision = asyncio.run(
        engine.evaluate(
            _definition("sync_skland_training"),
            {},
            context(skland_sync_service=FakeSyncService()),
        )
    )
    assert decision.requires_confirmation is True
    assert decision.confirmation_action == "sync_skland_training"
    assert decision.extra_payload["summary"] == "从森空岛同步全部干员练度"
    assert any("覆盖" in reason for reason in decision.reasons)


def test_status_does_not_require_confirmation() -> None:
    engine = PolicyEngine()
    decision = asyncio.run(
        engine.evaluate(
            _definition("get_skland_status"),
            {},
            context(skland_sync_service=FakeSyncService()),
        )
    )
    assert decision.requires_confirmation is False
    assert decision.confirmation_action is None
    assert decision.risk is RiskLevel.NONE
