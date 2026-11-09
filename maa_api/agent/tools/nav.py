"""界面导航图三个工具（M86 spec §4.3.1、§4.5.5）。

- ``where_am_i``：截一张图识别当前界面，纯读（不写库、不写文件、不加样本）；
- ``remember_screen``：把当前画面记成节点，按 ``last_seen`` 与审计学一条进入它的跳转；
- ``navigate_to``：一次调用完成最短路径、逐步执行、截图校验与走错重规划。

三个工具都从 ``app.state.nav_service`` 取图服务，``nav.enabled=false`` 时一律抛
``SERVICE_UNAVAILABLE``（``details.setting="nav.enabled"``）。视觉客户端与识别器每次
调用现建：视觉依赖（``model_router``/``cost_ledger``）缺失时 ``ModelVisionClient``
自己按不可用处理，不掉导航。``navigate_to`` 在 ``policy.ATOMIC_TOOL_NAMES`` 里，
一次导航只需要一次原子操作授权；``remember_screen`` 是显式工具，不受 ``nav.learn``
拦截（它只控制自动学习）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.nav import NavGraphService, Navigator, Recognizer
from maa_api.agent.nav.graph import NODE_ID_PATTERN
from maa_api.agent.nav.recognizer import ModelVisionClient
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = [
    "NavigateToParams",
    "RememberScreenParams",
    "WhereAmIParams",
    "navigate_to",
    "register_tools",
    "remember_screen",
    "where_am_i",
]


class WhereAmIParams(BaseModel):
    """``where_am_i`` 无参数（§4.3.1）。"""

    model_config = ConfigDict(extra="forbid")


class RememberScreenParams(BaseModel):
    """``remember_screen`` 的入参（§4.3.1）。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=1, max_length=64)
    kind: Literal["screen", "stage", "menu", "dialog"] | None = None


class NavigateToParams(BaseModel):
    """``navigate_to`` 的入参（§4.3.1）；``max_steps`` 只能把上限调小。"""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=2, max_length=48)
    max_steps: int | None = Field(default=None, ge=1, le=50)


def _app_state(context: ToolContext) -> Any:
    """当前请求的应用状态；导航服务与视觉依赖都挂在这里。"""
    return getattr(getattr(context.request, "app", None), "state", None)


def _service(context: ToolContext) -> NavGraphService:
    """取 ``app.state.nav_service``；缺失或 ``nav.enabled=false`` 都按服务不可用报。"""
    service = getattr(_app_state(context), "nav_service", None)
    if service is None or not get_settings().nav.enabled:
        raise AppError(
            ErrorCode.SERVICE_UNAVAILABLE,
            "界面导航图未启用",
            {"setting": "nav.enabled"},
        )
    return service


def _recognizer(service: NavGraphService, context: ToolContext) -> Recognizer:
    """现建视觉客户端与识别器：run_id/session_id 取自工具上下文（§4.2）。"""
    origin = context.origin
    vision = ModelVisionClient(
        _app_state(context),
        run_id=origin.run_id if origin is not None else None,
        session_id=context.session_id,
    )
    return Recognizer(service, vision)


def _node_payload(node: Any) -> dict[str, Any]:
    return {"id": node.id, "title": node.title, "kind": node.kind}


async def where_am_i(
    _params: WhereAmIParams, context: ToolContext
) -> dict[str, Any]:
    """纯读识别当前界面；返回 §4.3.1 的 node/method/distance/confidence/candidates。"""
    service = _service(context)
    navigator = Navigator(
        service,
        _recognizer(service, context),
        service.make_device(context),
        learn=False,
        max_steps=1,
        session_id=context.session_id,
    )
    recognition, _image = await navigator.where_am_i()
    await service.note_seen(context.session_id, recognition.node_id)

    nodes, _edges = await service.graph()
    by_id = {node.id: node for node in nodes}
    node = by_id.get(recognition.node_id) if recognition.node_id else None
    candidates = [
        {
            "id": node_id,
            "title": by_id[node_id].title,
            "distance": distance,
        }
        for node_id, distance in recognition.candidates[:3]
        if node_id in by_id
    ]
    return {
        "node": _node_payload(node) if node is not None else None,
        "method": recognition.method,
        "distance": recognition.distance,
        "confidence": recognition.confidence,
        "candidates": candidates,
    }


async def remember_screen(
    params: RememberScreenParams, context: ToolContext
) -> dict[str, Any]:
    """把当前画面记成节点并尽量学一条进入它的跳转（§4.5.5）。"""
    service = _service(context)
    settings = get_settings().nav
    if NODE_ID_PATTERN.fullmatch(params.node_id) is None:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "界面 id 不合法",
            {"field": "node_id"},
        )
    if not params.title.strip():
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "界面标题不能为空",
            {"field": "title"},
        )
    if (
        params.node_id.startswith("stage:")
        and params.kind is not None
        and params.kind != "stage"
    ):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "stage: 界面只能是 stage 类型",
            {"field": "kind"},
        )
    navigator = Navigator(
        service,
        _recognizer(service, context),
        service.make_device(context),
        learn=settings.learn,
        max_steps=settings.max_steps,
        db_session=context.db_session,
        session_id=context.session_id,
    )
    return await navigator.remember(
        node_id=params.node_id, title=params.title, kind=params.kind
    )


async def navigate_to(
    params: NavigateToParams, context: ToolContext
) -> dict[str, Any]:
    """一次导航到目标界面；``max_steps`` 取请求值与配置值的较小者（§4.5.4）。"""
    service = _service(context)
    settings = get_settings().nav
    max_steps = min(params.max_steps or settings.max_steps, settings.max_steps)
    navigator = Navigator(
        service,
        _recognizer(service, context),
        service.make_device(context),
        learn=settings.learn,
        max_steps=max_steps,
        db_session=context.db_session,
        session_id=context.session_id,
    )
    result = await navigator.navigate(params.target)
    node = await service.get_node(result.node_id)
    if node is None:  # pragma: no cover - navigate 只对图里的目标成功
        raise AppError(
            ErrorCode.NAV_NODE_NOT_FOUND,
            f"导航图里没有界面 {result.node_id}",
            {"target": result.node_id, "nearest": None},
        )
    return {
        "arrived": result.arrived,
        "node": _node_payload(node),
        "steps": result.steps,
        "elapsed_ms": result.elapsed_ms,
        "trail": result.trail,
        "learned": {
            "edges": result.learned_edges,
            "exemplars": result.learned_exemplars,
        },
    }


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``where_am_i`` / ``remember_screen`` / ``navigate_to``（§4.3.1）。"""
    registry.register(
        ToolDefinition(
            name="where_am_i",
            group="nav",
            risk=ToolRisk.SAFE,
            description=(
                "截图识别当前在哪个游戏界面，返回界面 id、标题与候选。"
                "动手操作前、或不能确定是否到了目标时先调用它；纯读，不改任何东西。"
            ),
            params_model=WhereAmIParams,
            handler=where_am_i,
            read_only=True,
            refetchable=True,
            device="read",
        )
    )
    registry.register(
        ToolDefinition(
            name="remember_screen",
            group="nav",
            risk=ToolRisk.SAFE,
            description=(
                "把当前画面记成导航图里的界面，并按最近的操作学一条进入它的跳转。"
                "导航图里找不到某个界面、手动走到之后用它记下来；关卡写成 stage:<关卡代号>，"
                "title 写中文名，下次就能一步到。"
            ),
            params_model=RememberScreenParams,
            handler=remember_screen,
            read_only=False,
            refetchable=False,
            device="none",
            rest_visible=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="navigate_to",
            group="nav",
            risk=ToolRisk.DANGEROUS,
            description=(
                "一次导航到目标界面：规划最短路径、逐步执行并截图校验，走错自动重规划、"
                "迷路自动回主界面。目标在导航图里时优先用它，不要拆成一步一步的 click。"
            ),
            params_model=NavigateToParams,
            handler=navigate_to,
            read_only=False,
            refetchable=False,
            device="exclusive",
        )
    )
