"""Agent 的账号数据工具（规格 §7.4、§7.7）：干员表、仓库、养成规划与计划。

分组 `account`。读接口 SAFE；`start_upgrade_plan` 每次确认（策略层单独分支，
确认卡片按摘要渲染，不把 payload 当普通 JSON 展开）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk

__all__ = [
    "CancelUpgradePlanParams",
    "GetSklandStatusParams",
    "GetDepotParams",
    "GetOperatorBoxParams",
    "ListUpgradePlansParams",
    "PlanUpgradeParams",
    "StartUpgradePlanParams",
    "SyncSklandTrainingParams",
    "cancel_upgrade_plan",
    "get_depot",
    "get_operator_box",
    "get_skland_status",
    "list_upgrade_plans",
    "plan_upgrade",
    "register_tools",
    "start_upgrade_plan",
    "sync_skland_training",
]


class GetOperatorBoxParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(
        default=None, max_length=32, description="按名字片段筛选（可选）。"
    )
    owned_only: bool = Field(default=True, description="只列已拥有的（默认 true）。")
    rarity_min: int | None = Field(default=None, ge=1, le=6, description="最低稀有度。")
    profession: str | None = Field(default=None, max_length=16, description="职业筛选。")
    elite_min: int | None = Field(default=None, ge=0, le=2, description="最低精英化。")
    limit: int = Field(default=50, ge=1, le=100, description="返回条数，默认 50，最多 100。")


class GetDepotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[str] | None = Field(
        default=None, max_length=50, description="要查的物品名或 itemId 列表；留空返回概览。"
    )
    category: str | None = Field(default=None, max_length=16, description="按分类筛选。")


class UpgradeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: str = Field(min_length=1, max_length=32)
    elite: int | None = Field(default=None, ge=0, le=2)
    skill_level: int | None = Field(default=None, ge=1, le=7)
    masteries: dict[Literal["1", "2", "3"], int] | None = None
    modules: list[dict[str, Any]] | None = Field(
        default=None, description="模组目标：[{\"type\": \"X\", \"level\": 2}]。"
    )


class PlanUpgradeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[UpgradeTarget] = Field(min_length=1, max_length=10)
    use_depot: bool = Field(default=True, description="把仓库库存算进需求（默认 true）。")


class StartUpgradePlanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, description="计划名字，用户能看到。")
    targets: list[UpgradeTarget] = Field(min_length=1, max_length=10)
    run_times: list[str] = Field(
        default_factory=lambda: ["08:00", "20:00"],
        min_length=1,
        max_length=4,
        description="每日运行时刻，`HH:MM`；默认 08:00 与 20:00。",
    )


class ListUpgradePlansParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CancelUpgradePlanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=36)


class GetSklandStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncSklandTrainingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


def register_tools(registry: ToolRegistry) -> None:
    """注册账号数据组工具（规格附录 A）。"""
    registry.register(
        ToolDefinition(
            name="get_operator_box",
            group="account",
            risk=ToolRisk.SAFE,
            description=(
                "查用户账号里的干员与练度（精英化、等级、潜能、技能等级、专精、模组），"
                "以及识别时间与是否过期。回答「我有没有这个干员」「我的练度够不够」时先查它；"
                "技能等级、专精、模组返回 null 表示**未识别**，别说成没升过技能或没有专精，"
                "用 sync_skland_training 从森空岛同步；"
                "数据缺失或过期时用 ask_user 建议用户在账号页绑定森空岛账号并同步练度。"
            ),
            params_model=GetOperatorBoxParams,
            handler=get_operator_box,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_depot",
            group="account",
            risk=ToolRisk.SAFE,
            description=(
                "查用户仓库里的材料数量与识别时间。做养成规划、判断材料够不够时用它；"
                "识别是游戏内任务，提交前要确认游戏已启动并停在主界面（见 task-preconditions）。"
            ),
            params_model=GetDepotParams,
            handler=get_depot,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="plan_upgrade",
            group="account",
            risk=ToolRisk.SAFE,
            description=(
                "算养成规划：给定目标（精英化、技能等级、专精、模组），算出需要哪些材料、"
                "去哪几关刷、每关刷几次、总共多少理智、大概几天。只计算，不提交任务。"
                "当前练度里未识别的维度不会被算成增量（也不按默认值兜底）：返回的 unknowns "
                "列出这些缺口，先同步练度或如实告诉用户，不要替用户假定。"
            ),
            params_model=PlanUpgradeParams,
            handler=plan_upgrade,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="start_upgrade_plan",
            group="account",
            risk=ToolRisk.SAFE,
            description=(
                "启动养成计划：先提交今天能打的部分，再创建一个每日定时任务按计划刷，"
                "材料齐了自动停。需要用户确认一次（确认卡片会列出今天的刷关清单、每日时刻、"
                "用药口径与预计天数）。练度里未识别的维度不会算成增量，返回的 unknowns "
                "列着这些缺口，要如实告诉用户而不是替他假定。"
            ),
            params_model=StartUpgradePlanParams,
            handler=start_upgrade_plan,
            read_only=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="list_upgrade_plans",
            group="account",
            risk=ToolRisk.SAFE,
            description="查养成计划的进度：目标、还缺什么、定时任务 id 与状态。",
            params_model=ListUpgradePlansParams,
            handler=list_upgrade_plans,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="cancel_upgrade_plan",
            group="account",
            risk=ToolRisk.SAFE,
            description="停止一个养成计划，并删除它对应的每日定时任务。",
            params_model=CancelUpgradePlanParams,
            handler=cancel_upgrade_plan,
            read_only=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="get_skland_status",
            group="account",
            risk=ToolRisk.SAFE,
            description=(
                "查森空岛账号绑定与练度同步的状态：有没有绑定、绑的是哪个角色、上次同步"
                "是什么时候、上次失败原因。用户问「练度是不是最新的」「同步成功了吗」"
                "「森空岛绑了吗」时用它；没绑定时返回 bound=false，用 ask_user 引导用户"
                "到账号页扫码绑定，不要自己编造练度。"
            ),
            params_model=GetSklandStatusParams,
            handler=get_skland_status,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="sync_skland_training",
            group="account",
            risk=ToolRisk.CONDITIONAL,
            description=(
                "从森空岛同步账号里**全部**干员的练度（精英化、等级、潜能、技能等级、"
                "专精、模组、信赖）并写进账号表，这是账号干员数据的唯一来源；用户在问养成"
                "规划却发现练度未识别、或说「同步一下练度」「更新练度」时用它。"
                "它不占设备、几秒内跑完，但会用官方账号数据覆盖整表练度，"
                "所以每次都要用户确认；"
                "没绑定森空岛时先让用户去账号页扫码绑定（见 get_skland_status）。"
            ),
            params_model=SyncSklandTrainingParams,
            handler=sync_skland_training,
            read_only=False,
        )
    )


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------
async def get_operator_box(params: GetOperatorBoxParams, context: ToolContext) -> dict[str, Any]:
    service = _service(context, "box_service", "maa_api.services.account.box", "OperatorBoxService")
    return await service.list_operators(
        query=params.query,
        owned_only=params.owned_only,
        rarity_min=params.rarity_min,
        profession=params.profession,
        elite_min=params.elite_min,
        limit=params.limit,
    )


async def get_depot(params: GetDepotParams, context: ToolContext) -> dict[str, Any]:
    service = _service(context, "depot_service", "maa_api.services.account.depot", "DepotService")
    if params.items:
        return await service.list_items(query=None, limit=len(params.items), offset=0)
    return await service.summary()


async def plan_upgrade(params: PlanUpgradeParams, context: ToolContext) -> dict[str, Any]:
    service = _planner(context)
    return await service.as_dict_plan(_targets(params.targets), use_depot=params.use_depot)


async def start_upgrade_plan(params: StartUpgradePlanParams, context: ToolContext) -> dict[str, Any]:
    service = _service(
        context, "upgrade_plan_service", "maa_api.services.account.plans", "UpgradePlanService"
    )
    return await service.start(
        name=params.name,
        targets=_targets(params.targets),
        run_times=params.run_times,
        context=context,
    )


async def list_upgrade_plans(params: ListUpgradePlansParams, context: ToolContext) -> dict[str, Any]:
    service = _service(
        context, "upgrade_plan_service", "maa_api.services.account.plans", "UpgradePlanService"
    )
    return await service.list_plans()


async def cancel_upgrade_plan(params: CancelUpgradePlanParams, context: ToolContext) -> dict[str, Any]:
    service = _service(
        context, "upgrade_plan_service", "maa_api.services.account.plans", "UpgradePlanService"
    )
    return await service.cancel(params.plan_id)


async def get_skland_status(
    params: GetSklandStatusParams, context: ToolContext
) -> dict[str, Any]:
    return await _skland_service(context, "sync").status()


async def sync_skland_training(
    params: SyncSklandTrainingParams, context: ToolContext
) -> dict[str, Any]:
    """跑一轮森空岛同步；几秒内返回摘要（不占设备、不排队）。"""
    return await _skland_service(context, "sync").sync()


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _targets(items: list[UpgradeTarget]) -> list[dict[str, Any]]:
    """工具参数 → 规划层的目标形状。"""
    targets: list[dict[str, Any]] = []
    for item in items:
        payload = item.model_dump(exclude_none=True)
        masteries = payload.get("masteries")
        if isinstance(masteries, dict):
            payload["masteries"] = {int(key): value for key, value in masteries.items()}
        modules = payload.get("modules")
        if isinstance(modules, list):
            payload["modules"] = [
                (str(module.get("type")), int(module.get("level", 1)))
                for module in modules
                if isinstance(module, dict) and module.get("type")
            ]
        targets.append(payload)
    return targets


def _planner(context: ToolContext) -> Any:
    service = _state(context, "planner_service")
    if service is not None:
        return service
    from maa_api.services.account.planner import default_upgrade_planner

    return default_upgrade_planner()


def _skland_service(context: ToolContext, kind: str) -> Any:
    """森空岛服务：只认 ``main.py`` 的装配名（``skland_sync_service`` / ``skland_login_service``）。

    它带着凭据存储与出站客户端，不是一个会话工厂能现造的，所以拿不到就报装配错误，
    不走 `_service` 的兜底路径。
    """
    name = {"sync": "skland_sync_service", "login": "skland_login_service"}.get(kind)
    service = _state(context, name) if name else None
    if service is None:
        from maa_api.domain.errors import AppError, ErrorCode

        raise AppError(ErrorCode.INTERNAL_ERROR, f"森空岛服务尚未装配（缺少 {name}）")
    return service


def _service(context: ToolContext, state_name: str, module: str, class_name: str) -> Any:
    service = _state(context, state_name)
    if service is not None:
        return service
    import importlib

    loaded = importlib.import_module(module)
    factory = getattr(loaded, f"default_{state_name}", None)
    if callable(factory):
        return factory()
    return getattr(loaded, class_name)(_session_factory(context))


def _session_factory(context: ToolContext) -> Any:
    """账号数据的服务需要会话工厂；从应用装配取，取不到就抛装配错误。"""
    state = getattr(getattr(context.request, "app", None), "state", None)
    factory = getattr(state, "session_factory", None)
    if factory is None:
        from maa_api.domain.errors import AppError, ErrorCode

        raise AppError(ErrorCode.INTERNAL_ERROR, "账号数据服务尚未装配（缺少 session_factory）")
    return factory


def _state(context: ToolContext, name: str) -> Any:
    state = getattr(getattr(context.request, "app", None), "state", None)
    return getattr(state, name, None)
