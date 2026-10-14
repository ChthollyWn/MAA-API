"""账号数据端点（M27，规格 §7.5、附录 C）。

``/api/account`` 前缀下的端点：概要、干员表（筛选 + 分页）、单条干员、游戏资源图、
仓库，以及 M31 补的养成计划列表/单条/取消。M36 的三条练度扫描端点（受理、看进度、
取消）已在 M42 §4.9 退役，练度读走 ``/api/skland``；M47 把内核干员识别（``OperBox``）
与手动编辑一起下线，干员数据**只读**、唯一来源是森空岛同步（docs/13 §8.70）。

路由层只做三件事：解析查询参数、取服务、把服务返回的 dict 原样透出。

服务未启动（lifespan 还在装配）时统一回 503 ``SERVICE_UNAVAILABLE``，与其它路由同款。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel, Field

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.assets import ASSET_KINDS, GameAssetService
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.account.depot import DepotService
from maa_api.services.account.plans import UpgradePlanService

__all__ = [
    "AccountDepotList",
    "AccountDepotRow",
    "AccountOperatorList",
    "AccountOperatorRow",
    "router",
]

router = APIRouter(prefix="/api/account", tags=["account"])

#: 养成计划的三态（规格 §7.7，落在 ``upgrade_plan.status``）；非法值由 FastAPI 挡成
#: 422 ``VALIDATION_ERROR``，路由层不自己解析。
PlanStatus = Literal["active", "completed", "cancelled"]


class AccountOperatorRow(BaseModel):
    """干员行：docs/05 §6.20 的字段清单，与单条读取同形。

    M47 起这张表**只由森空岛练度同步写**（``field_sources`` 因此基本只有 ``skland``；
    ``recognition`` / ``manual`` / ``agent`` / ``training`` 只可能出现在 M47 之前的历史行上，
    而迁移 `0022` 已经把老行清掉了）。练度六列（``elite`` / ``level`` / ``potential`` /
    ``skill_level`` / ``masteries`` / ``modules``）可以是 ``null`` ＝ **未识别**：那次同步
    没给这一项，或上游给了越界值。别把 ``null`` 读成 0 级 / 技能 1 级 / 无专精。
    ``name``、``rarity``、``profession`` 在映射不到的干员上也可以是 ``null``。
    """

    char_id: str
    name: str | None = None
    rarity: int | None = None
    profession: str | None = None
    own: bool
    elite: int | None = None
    level: int | None = None
    potential: int | None = None
    skill_level: int | None = None
    masteries: dict[str, int] | None = None
    modules: dict[str, int] | None = None
    favor_percent: int | None = None
    #: 按槽位的技能 id（M43 §4.3）：卡片用 ``char_skill/{skill_id}.png`` 拼技能图标。
    #: ``null`` ＝ 不知道（没同步过、或上游没给），不是空列表。
    skill_ids: list[str] | None = None
    #: 模组明细（M43 §4.3）：``[{type, level, equip_id, branch, label, type_icon}]``，
    #: 供页面显示 ``CCR-X`` 这类标签；``modules`` 仍只是 ``{"Y": 3}``。
    module_details: list[AccountModuleDetail] | None = None
    field_sources: dict[str, str] = Field(default_factory=dict)
    recognized_at: str | None = None
    updated_at: str | None = None


class AccountModuleDetail(BaseModel):
    """模组明细（M43 §4.3）：卡片显示 ``CCR-X`` 与等级的依据。

    ``type`` 是类型字母（`X` / `Y` / `D` / `A`），``branch`` 是分支码（`ccr` / `exe` …），
    ``label`` 是页面直接显示的标签（`CCR-X`），``type_icon`` 是上游 `typeIcon` 原值
    （`ccr-y`，以后要用第三方图标时按它拼 URL）。``equip_id`` 是模组 id。
    """

    type: str
    level: int
    equip_id: str | None = None
    branch: str | None = None
    label: str | None = None
    type_icon: str | None = None


class AccountOperatorList(BaseModel):
    """干员表响应（docs/05 §6.20：列表用 ``{items, total, limit, offset}``）。"""

    items: list[AccountOperatorRow]
    total: int
    limit: int
    offset: int


class AccountDepotRow(BaseModel):
    """仓库行：物品 id、名字、数量、分类与识别时间（规格 §7.5 的只读列表）。

    ``count`` 允许 ``null`` 是**防御性**的：迁移 `0021` 起这一列可空，而写这一列的那条
    自识别链路已于 2026-10-13 关停、2026-10-14 连实现一起删除（见 `docs/13` §8.68），
    正常路径上不会有这种行 —— 列表读侧也已排除它们。真出现时页面显示「—」，
    **不许当成 0**（「仓库里没有这件道具」与「不知道有几件」是两件事）。
    """

    item_id: str
    name: str | None = None
    count: int | None = None
    category: str | None = None
    recognized_at: str | None = None


class AccountDepotList(BaseModel):
    """仓库响应（docs/05 §6.20：列表用 ``{items, total, limit, offset}``）。

    ``items`` 只含**有明确数量**的行（``count IS NOT NULL``）：用户 2026-10-13 的口径是
    「仓库页只显示内核识别到的、有明确数量的物品」，历史上自识别写下的 ``NULL`` 行读侧
    排除（见 `docs/13` §8.68）。``categories`` 是这份快照里出现过的全部分类（按名字排序），
    与 ``category`` / ``query`` 无关，页面用它渲染分类下拉（M34），口径与 ``items`` 一致。
    """

    items: list[AccountDepotRow]
    total: int
    limit: int
    offset: int
    categories: list[str] = Field(default_factory=list)


def _box_service(request: Request) -> OperatorBoxService:
    """取 lifespan 装配的干员服务。

    同时认 ``account_box_service`` 与 ``box_service`` 两个键：前者是本路由文档里写的
    名字（测试自搭 app 常用），后者是 ``main.py`` 的装配名（工具层与注入段也用它）。
    两边都不存在时是「服务还没起来」，503。
    """
    state = request.app.state
    service = getattr(state, "account_box_service", None) or getattr(
        state, "box_service", None
    )
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "账号数据功能还在启动中，请稍后重试")
    return service


def _asset_service(request: Request) -> GameAssetService:
    """取 lifespan 装配的游戏资源代理服务（M43 后续）。

    没装配时**临时建一个**：它无状态（只有磁盘缓存与一个 httpx 客户端），测试与
    早期启动路径不必都走一遍 lifespan；长时间运行的实例仍由 lifespan 管，退出时关闭。
    """
    state = request.app.state
    service = getattr(state, "game_asset_service", None)
    if service is None:
        service = GameAssetService()
        state.game_asset_service = service
    return service


def _depot_service(request: Request) -> DepotService:
    """取 lifespan 装配的仓库服务；键名同 :func:`_box_service`。"""
    state = request.app.state
    service = getattr(state, "account_depot_service", None) or getattr(
        state, "depot_service", None
    )
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "账号数据功能还在启动中，请稍后重试")
    return service


def _plan_service(request: Request) -> UpgradePlanService:
    """取 lifespan 装配的养成计划服务；键名约定同 :func:`_box_service`。

    ``upgrade_plan_service`` 是 ``main.py`` 的装配名，``account_upgrade_plan_service``
    是本路由文档里写的名字（测试自搭 app 常用）。
    """
    state = request.app.state
    service = getattr(state, "upgrade_plan_service", None) or getattr(
        state, "account_upgrade_plan_service", None
    )
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "养成计划功能还在启动中，请稍后重试")
    return service


async def _plan_wires(request: Request) -> list[dict[str, Any]]:
    """全部计划的 wire 列表；``list_plans()`` 会顺带刷新进行中计划的进度。

    服务层只提供 :meth:`UpgradePlanService.list_plans`（没有单条读取），所以单条读取
    也走这里过滤，列表与单条的形状天然一致。
    """
    listing = await _plan_service(request).list_plans()
    plans = listing.get("plans")
    return list(plans) if isinstance(plans, list) else []


@router.get(
    "/summary",
    summary="读取账号数据概要",
    description=(
        "干员与仓库的计数、上次写入时间与是否过期。\n\n"
        "- 干员侧的唯一来源是森空岛练度同步（M47 起），所以 `box.recognized_at` 是"
        "上次同步时间，阈值看 `agent.box_stale_days`（默认 14 天）\n"
        "- 仓库侧看 `agent.depot_stale_days`（默认 3 天）\n"
        "- 从未同步 / 从未识别时 `recognized_at` 为 `null` 且 `stale` 为 `true`"
    ),
    responses=error_responses("UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_account_summary(request: Request) -> dict[str, Any]:
    box = await _box_service(request).summary()
    depot = await _depot_service(request).summary()
    return {"box": box, "depot": depot}


@router.get(
    "/operators",
    response_model=AccountOperatorList,
    summary="读取干员表",
    description=(
        "账号干员列表，带筛选与分页。\n\n"
        "- 数据来自森空岛练度同步（M47 起唯一来源），只读\n"
        "- 默认只返回已拥有的干员（`owned_only=true`）；未拥有的行也留在表里"
        "（那一轮同步的 `chars` 里没有它）\n"
        "- `total` 是筛选后的总数，不是当前页条数\n"
        "- 每行的 `field_sources` 标出各字段的来源（M47 之后基本只有 `skland`）"
    ),
    responses=error_responses("VALIDATION_ERROR", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_account_operators(
    request: Request,
    query: str | None = Query(default=None, description="干员名片段"),
    owned_only: bool = Query(default=True, description="只返回已拥有的干员"),
    rarity_min: int | None = Query(default=None, ge=1, le=6, description="最低稀有度"),
    profession: str | None = Query(default=None, description="职业，如 近卫 / 医疗"),
    elite_min: int | None = Query(default=None, ge=0, le=2, description="最低精英化等级"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AccountOperatorList:
    payload = await _box_service(request).list_operators(
        query=query,
        owned_only=owned_only,
        rarity_min=rarity_min,
        profession=profession,
        elite_min=elite_min,
        limit=limit,
        offset=offset,
    )
    return AccountOperatorList(**payload)


@router.get(
    "/assets/{kind}/{asset_id}",
    summary="读取游戏资源图（干员头像 / 技能图标 / 道具图标）",
    description=(
        "后端代理官方 CDN 的游戏美术资源，并按内容落盘缓存。\n\n"
        "- `kind`：`avatar`（干员头像）、`skill`（技能图标）或 `item`（道具图标）\n"
        "- `asset_id`：`avatar` 是干员 id（`char_002_amiya`）、`skill` 是技能 id"
        "（`skchr_amiya_3`）、`item` 是**道具 id**（`3003`、`mod_unlock_token`）——"
        "道具的图标文件名由服务端从本机 `item_index.json` 解析，前端不必知道\n"
        "- 走服务端取图的原因：官方 CDN 有防盗链，浏览器带 `Referer` 会被 403\n"
        "- 响应可长期缓存（`Cache-Control: public, max-age=604800`），命中缓存时不打上游"
    ),
    responses=error_responses("ACCOUNT_DATA_NOT_FOUND", "SKLAND_UPSTREAM_FAILED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
    response_class=Response,
)
async def get_game_asset(
    request: Request,
    kind: Annotated[str, Path(description="资源类型：avatar、skill 或 item")],
    asset_id: Annotated[
        str,
        Path(
            description=(
                "干员 id（如 char_002_amiya）、技能 id（如 skchr_amiya_3）"
                "或道具 id（如 3003）"
            )
        ),
    ],
) -> Response:
    if kind not in ASSET_KINDS:
        raise AppError(
            ErrorCode.ACCOUNT_DATA_NOT_FOUND,
            "没有这类游戏资源",
            {"kind": kind},
        )
    payload = await _asset_service(request).get(kind, asset_id)
    return Response(
        content=payload,
        media_type="image/png",
        # 一周：游戏资源只随版本更新，id 相同内容不会变；换 id 就是换 URL。
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.get(
    "/operators/{char_id}",
    summary="读取单名干员",
    description="按 `char_id` 读一条干员记录（外部 API 读取方与卡片详情用）。",
    responses=error_responses("ACCOUNT_DATA_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_account_operator(
    request: Request,
    char_id: str = Path(description="干员 id，如 char_002_amiya"),
) -> dict[str, Any]:
    return await _box_service(request).get_operator(char_id)



@router.get(
    "/depot",
    response_model=AccountDepotList,
    summary="读取仓库",
    description=(
        "仓库识别结果，按物品分类分组展示的只读列表。\n\n"
        "- `category` 与 `query` 都是筛选条件，`total` 是筛选后的总数\n"
        "- `categories` 是这份快照里出现过的全部分类（按名字排序），不受两个筛选"
        "条件影响；页面用它渲染分类下拉\n"
        "- 只返回**有明确数量**的物品（内核 `Depot` 那一趟的结果，约 77 件）：M45 的"
        "自识别已于 2026-10-13 关停、2026-10-14 删除实现，它写下的 `count = NULL` 行"
        "读侧一律排除（`total` 与 `categories` 同口径）\n"
        "- `count` 仍是可空列：真出现 `null` 时页面显示「—」，不许当成 0\n"
        "- 识别时间是整表替换时写下的，`stale` 按 `agent.depot_stale_days` 判定"
    ),
    responses=error_responses("VALIDATION_ERROR", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_account_depot(
    request: Request,
    category: str | None = Query(default=None, description="物品分类"),
    query: str | None = Query(default=None, description="物品名片段"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AccountDepotList:
    payload = await _depot_service(request).list_items(
        category=category, query=query, limit=limit, offset=offset
    )
    return AccountDepotList(**payload)


# ---------------------------------------------------------------------------
# 养成计划（M31 补口，规格 §7.7）
# ---------------------------------------------------------------------------


@router.get(
    "/upgrade-plans",
    summary="列出养成计划",
    description=(
        "养成计划列表，`items` 的形状与 Agent 工具 `list_upgrade_plans` 一致。\n\n"
        "- 顺带重算进行中计划的进度：`active` 且材料已齐的计划会在这里变成 `completed`\n"
        "- `status` 只接受 `active` / `completed` / `cancelled`，缺省返回全部\n"
        "- `total` 是筛选后的条数"
    ),
    responses=error_responses("VALIDATION_ERROR", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_account_upgrade_plans(
    request: Request,
    status: PlanStatus | None = Query(
        default=None, description="按状态筛选：active / completed / cancelled"
    ),
) -> dict[str, Any]:
    plans = [
        plan
        for plan in await _plan_wires(request)
        if status is None or plan.get("status") == status
    ]
    return {"items": plans, "total": len(plans)}


@router.get(
    "/upgrade-plans/{plan_id}",
    summary="读取养成计划",
    description=(
        "按 `plan_id` 读一条养成计划，返回与列表 `items` 相同的形状。\n\n"
        "- 计划不存在时 404 `UPGRADE_PLAN_NOT_FOUND`（与服务层同一个码）"
    ),
    responses=error_responses("UPGRADE_PLAN_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_account_upgrade_plan(
    request: Request,
    plan_id: str = Path(description="计划 id"),
) -> dict[str, Any]:
    for plan in await _plan_wires(request):
        if str(plan.get("id")) == plan_id:
            return plan
    raise AppError(
        ErrorCode.UPGRADE_PLAN_NOT_FOUND,
        f"养成计划不存在：{plan_id}",
        {"plan_id": plan_id},
    )


@router.delete(
    "/upgrade-plans/{plan_id}",
    summary="取消养成计划",
    description=(
        "取消一条养成计划，等价于 Agent 工具 `cancel_upgrade_plan`。\n\n"
        "- 删除计划名下的每日定时任务；定时任务已经不在了也不报错\n"
        "- 计划状态置为 `cancelled`，返回取消后的计划\n"
        "- 计划不存在时 404 `UPGRADE_PLAN_NOT_FOUND`"
    ),
    responses=error_responses("UPGRADE_PLAN_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def cancel_account_upgrade_plan(
    request: Request,
    plan_id: str = Path(description="计划 id"),
) -> dict[str, Any]:
    return await _plan_service(request).cancel(plan_id)
