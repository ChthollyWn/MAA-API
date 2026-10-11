"""Agent 的账号数据工具（规格 §7.4、§7.7）：干员表、仓库、养成规划与计划。

分组 `account`。读接口 SAFE；`update_operator_box` 与 `start_upgrade_plan` 每次确认
（策略层单独分支，确认卡片按差异/摘要渲染，不把 payload 当普通 JSON 展开）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk

__all__ = [
    "CancelTrainingScanParams",
    "CancelUpgradePlanParams",
    "GetDepotParams",
    "GetOperatorBoxParams",
    "ListUpgradePlansParams",
    "PlanUpgradeParams",
    "ScanOperatorTrainingParams",
    "StartUpgradePlanParams",
    "UpdateOperatorBoxParams",
    "cancel_training_scan",
    "cancel_upgrade_plan",
    "get_depot",
    "get_operator_box",
    "list_upgrade_plans",
    "plan_upgrade",
    "register_tools",
    "scan_operator_training",
    "start_upgrade_plan",
    "update_operator_box",
]

#: 干员编辑条目的字段（与 `OperatorBoxService.patch_operator` 的可编辑字段一致）。
class OperatorChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: str = Field(
        min_length=1, max_length=32, description="干员名或 char_id。"
    )
    own: bool | None = Field(default=None, description="是否拥有。")
    elite: int | None = Field(default=None, ge=0, le=2, description="精英化 0–2。")
    level: int | None = Field(default=None, ge=1, le=90, description="等级。")
    potential: int | None = Field(default=None, ge=1, le=6, description="潜能 1–6。")
    skill_level: int | None = Field(default=None, ge=1, le=7, description="技能等级 1–7。")
    masteries: dict[Literal["1", "2", "3"], int] | None = Field(
        default=None, description="专精：技能序号 → 0–3（只在精二且技能 7 级时可填）。"
    )
    modules: dict[str, int] | None = Field(
        default=None,
        description="模组：类型（X/Y/D/A）→ 等级 1–3（只在精二时可填）。",
    )

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "OperatorChange":
        if not self.model_fields_set - {"operator"}:
            raise ValueError("每一项改动至少要给一个字段")
        return self


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


class UpdateOperatorBoxParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[OperatorChange] = Field(
        min_length=1, max_length=20, description="要改的干员与字段，1–20 条。"
    )
    reason: str = Field(
        min_length=1, max_length=200, description="为什么要改（会显示在确认卡片上）。"
    )


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


class ScanOperatorTrainingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(
        default="single",
        pattern="^(single|full)$",
        description=(
            "扫描类型：single＝按名单点名扫（默认，受 limit 限制，几十分钟以内）；"
            "full＝全量扫描，从列表第一名开始把每个人的详情页都扫一遍（忽略 limit / "
            "char_ids / include_plan_targets，可能要跑几十分钟，只在用户明确要求"
            "「全量扫描」「扫全部干员」时用）。"
        ),
    )
    limit: int = Field(
        default=40, ge=1, le=200, description="这一轮最多扫多少位干员，默认 40，最多 200。"
    )
    include_plan_targets: bool = Field(
        default=True, description="把养成计划里的目标干员也算进来（默认 true）。"
    )
    char_ids: list[str] | None = Field(
        default=None,
        max_length=200,
        description="只扫这些干员（char_id 或名字），留空＝按账号里的干员排。",
    )


class CancelTrainingScanParams(BaseModel):
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
                "技能等级、专精、模组返回 null 表示**未识别**（内核的干员识别只给是否拥有、"
                "精英化、等级、潜能），别说成没升过技能或没有专精，用 update_operator_box "
                "补录或让用户确认；"
                "数据缺失或过期时返回 suggest_recognition，用 ask_user 建议用户跑一次干员识别；"
                "识别是游戏内任务，提交前要确认游戏已启动并停在主界面（见 task-preconditions）。"
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
                "仓库数据缺失或过期时返回 suggest_recognition；"
                "识别是游戏内任务，提交前要确认游戏已启动并停在主界面（见 task-preconditions）。"
            ),
            params_model=GetDepotParams,
            handler=get_depot,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="update_operator_box",
            group="account",
            risk=ToolRisk.SAFE,
            description=(
                "修改账号里的干员数据（是否拥有、精英化、等级、潜能、技能等级、专精、模组）。"
                "技能等级、专精、模组识别不出来，只能靠它补录；"
                "用户明确要求纠正或补录时才用；每次修改都会弹确认卡片展示改动前后的差异。"
            ),
            params_model=UpdateOperatorBoxParams,
            handler=update_operator_box,
            read_only=False,
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
                "列出这些缺口，先补录或如实告诉用户，不要替用户假定。"
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
            name="scan_operator_training",
            group="account",
            risk=ToolRisk.CONDITIONAL,
            description=(
                "跑一轮练度扫描：逐个点开干员详情页，读出技能等级、专精、精英化与模组等级"
                "并写进账号表。干员识别只给是否拥有、精英化、等级、潜能，这几项只能靠它补；"
                "用户在问养成规划却发现这些字段未识别时，用它把未知补上。"
                "`mode=full` 是全量扫描：从列表第一名开始把账号里每个人的详情页都扫一遍，"
                "用户说「全量扫描」「把所有人的练度都扫一遍」时才用，它忽略 limit / char_ids，"
                "可能要跑几十分钟（比 single 慢得多）。"
                "它会占着设备一段时间（用户这期间不能做别的），所以每次都要用户确认；"
                "提交后立刻返回，扫描在后台跑，进度可以用 get_operator_box 或账号接口查。"
                "游戏没启动或没停在主界面时先看 task-preconditions。"
            ),
            params_model=ScanOperatorTrainingParams,
            handler=scan_operator_training,
            read_only=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="cancel_training_scan",
            group="account",
            risk=ToolRisk.SAFE,
            description=(
                "停掉正在跑的练度扫描。已经读到的练度会保留；"
                "用户说「别扫了」「停下」时用它。"
            ),
            params_model=CancelTrainingScanParams,
            handler=cancel_training_scan,
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


async def update_operator_box(params: UpdateOperatorBoxParams, context: ToolContext) -> dict[str, Any]:
    """改干员表：逐条落到 `patch_operator`，字段来源标 `agent`。"""
    service = _service(context, "box_service", "maa_api.services.account.box", "OperatorBoxService")
    results: list[dict[str, Any]] = []
    for change in params.changes:
        payload = change.model_dump(exclude_unset=True, exclude_none=True)
        operator = payload.pop("operator")
        results.append(await service.patch_operator(operator, payload, source="agent"))
    return {
        "updated": len(results),
        "operators": results,
        "reason": params.reason,
    }


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


async def scan_operator_training(
    params: ScanOperatorTrainingParams, context: ToolContext
) -> dict[str, Any]:
    """提交一轮练度扫描。服务要设备与流水线，只能从装配里拿。"""
    service = _scan_service(context)
    return await service.submit(
        mode=params.mode,
        limit=params.limit,
        char_ids=params.char_ids,
        include_plan_targets=params.include_plan_targets,
    )


async def cancel_training_scan(
    params: CancelTrainingScanParams, context: ToolContext
) -> dict[str, Any]:
    service = _scan_service(context)
    return await service.cancel()


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


def _scan_service(context: ToolContext) -> Any:
    """练度扫描服务：它要设备、流水线与资源层，不是一个会话工厂能造出来的。

    所以只认装配名（``main.py`` 里的 ``training_scan_service``），拿不到就报装配错误，
    不走 `_service` 的「按会话工厂现造」兜底。
    """
    service = _state(context, "training_scan_service") or _state(
        context, "account_training_scan_service"
    )
    if service is None:
        from maa_api.domain.errors import AppError, ErrorCode

        raise AppError(
            ErrorCode.INTERNAL_ERROR, "练度扫描服务尚未装配（缺少 training_scan_service）"
        )
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
