"""账号数据端点（M27，规格 §7.5、附录 C）。

六个只读 / 编辑端点，前缀 ``/api/account``：概要与是否过期、干员表（筛选 + 分页）、
手动编辑、识别差异（列表与「知道了」）、仓库。

路由层只做三件事：解析查询参数与请求体、取服务、把服务返回的 dict 原样透出。
字段级编辑约束（精英化上限、专精前置、模组类型与等级）在
:class:`~maa_api.services.account.box.OperatorBoxService` 里判定，违反时抛
422 ``VALIDATION_ERROR``（``details.field`` 指出是哪一个字段）；路由层不重复实现。
请求体只接受可编辑字段，未知字段由 ``extra="forbid"`` 拦成 422。

服务未启动（lifespan 还在装配）时统一回 503 ``SERVICE_UNAVAILABLE``，与其它路由同款。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.account.depot import DepotService
from maa_api.services.account.rules import (
    MAX_LEVEL,
    MAX_MASTERY_LEVEL,
    MAX_MODULE_LEVEL,
    MAX_POTENTIAL,
    MAX_SKILL_LEVEL,
    MODULE_TYPES,
)

__all__ = ["OperatorDiffDismissRequest", "OperatorPatchRequest", "router"]

router = APIRouter(prefix="/api/account", tags=["account"])

#: ``PATCH`` 的 ``source``：只有手动与 Agent 两条写入途径（识别写入固定标
#: ``recognition``，见服务层），所以这里用 Literal 而不是自由字符串。
WriteSource = Literal["manual", "agent"]

#: 专精的键是技能序号字符串（wire 形状 ``{"1": 3}``）：用约束把它钉在 1–3，
#: 越界的键由请求模型挡成 422，服务层不用再猜 ``1`` 与 ``"1"`` 两种写法。
MasteryKey = Annotated[str, StringConstraints(pattern=r"^[123]$")]
#: 模组类型只能是 X / Y / D / A（规格 §7.5）。
ModuleType = Annotated[str, StringConstraints(pattern=r"^[XYDA]$")]


class OperatorPatchRequest(BaseModel):
    """手动编辑一名干员的请求体：只接受可编辑字段，未给的字段不动。

    取值域在这里做第一层（类型与范围），跨字段约束（精英化与稀有度、专精与技能等级、
    模组与精英化）在服务层做——它们要看行里既有的值。
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"own": True, "elite": 2, "level": 90, "potential": 6},
                {
                    "elite": 2,
                    "level": 80,
                    "skill_level": 7,
                    "masteries": {"1": 3, "2": 3, "3": 0},
                    "modules": {"X": 3},
                    "source": "manual",
                },
            ]
        },
    )

    # 用 StrictBool：宽松模式会把 "yes" / 1 静默当 True，页面传错类型时应该报错。
    own: StrictBool | None = Field(default=None, description="是否拥有")
    elite: int | None = Field(default=None, ge=0, le=2, description="精英化等级，上限按稀有度")
    level: int | None = Field(default=None, ge=0, le=MAX_LEVEL, description="等级")
    potential: int | None = Field(default=None, ge=0, le=MAX_POTENTIAL, description="潜能")
    skill_level: int | None = Field(
        default=None, ge=1, le=MAX_SKILL_LEVEL, description="技能等级 1–7"
    )
    masteries: dict[MasteryKey, int] | None = Field(
        default=None,
        description=(
            "专精等级：技能序号 1–3 → 0–"
            f"{MAX_MASTERY_LEVEL}；只在精二且技能 7 级时可填"
        ),
    )
    modules: dict[ModuleType, int] | None = Field(
        default=None,
        description=(
            "模组等级：类型 "
            f"{'/'.join(MODULE_TYPES)} → 1–{MAX_MODULE_LEVEL}；只在精二时可填"
        ),
    )
    source: WriteSource = Field(
        default="manual", description="字段来源标记；Agent 工具写 agent，页面写 manual"
    )


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


def _depot_service(request: Request) -> DepotService:
    """取 lifespan 装配的仓库服务；键名同 :func:`_box_service`。"""
    state = request.app.state
    service = getattr(state, "account_depot_service", None) or getattr(
        state, "depot_service", None
    )
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "账号数据功能还在启动中，请稍后重试")
    return service


@router.get(
    "/summary",
    summary="读取账号数据概要",
    description=(
        "干员与仓库的计数、识别时间与是否过期，以及未处理的识别差异条数。\n\n"
        "- 过期阈值由 `agent.box_stale_days`（默认 14 天）与 `agent.depot_stale_days`"
        "（默认 3 天）决定\n"
        "- 从未识别时 `recognized_at` 为 `null` 且 `stale` 为 `true`"
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
    summary="读取干员表",
    description=(
        "账号识别到的干员列表，带筛选与分页。\n\n"
        "- 默认只返回已拥有的干员（`owned_only=true`）；未拥有的行来自识别的"
        " `all_opers`\n"
        "- `total` 是筛选后的总数，不是当前页条数\n"
        "- 每行的 `field_sources` 标出各字段来自识别、手动还是 Agent"
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
) -> dict[str, Any]:
    return await _box_service(request).list_operators(
        query=query,
        owned_only=owned_only,
        rarity_min=rarity_min,
        profession=profession,
        elite_min=elite_min,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/operators/{char_id}",
    summary="读取单名干员",
    description="按 `char_id` 读一条干员记录（编辑抽屉打开时用）。",
    responses=error_responses("ACCOUNT_DATA_NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_account_operator(
    request: Request,
    char_id: str = Path(description="干员 id，如 char_002_amiya"),
) -> dict[str, Any]:
    return await _box_service(request).get_operator(char_id)


@router.patch(
    "/operators/{char_id}",
    summary="编辑干员数据",
    description=(
        "手动编辑一名干员的字段，只写请求体里给出的字段。\n\n"
        "- 精英化上限按稀有度：1–2★ 精零、3★ 精一、4–6★ 精二\n"
        "- 专精只在精二且技能等级 7 时可填；模组只在精二时可填，类型 X/Y/D/A、"
        "等级 1–3\n"
        "- 写入后这些字段的 `field_sources` 标 `manual`（或 `source=agent`）\n"
        "- 下次识别若覆盖了手动值且值不同，会记一条差异并在"
        " `GET /api/account/operator-diffs` 里列出\n"
        "- 违反字段约束返回 422 `VALIDATION_ERROR`，`details.field` 指出字段"
    ),
    responses=error_responses(
        "ACCOUNT_DATA_NOT_FOUND", "VALIDATION_ERROR", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def patch_account_operator(
    payload: OperatorPatchRequest,
    request: Request,
    char_id: str = Path(description="干员 id，如 char_002_amiya"),
) -> dict[str, Any]:
    values = payload.model_dump(exclude_unset=True, exclude={"source"})
    return await _box_service(request).patch_operator(
        char_id, values, source=payload.source
    )


@router.get(
    "/operator-diffs",
    summary="读取识别差异",
    description=(
        "识别覆盖掉手动值的差异列表：干员、字段、手动值、识别值、时间。\n\n"
        "- 默认只返回未处理的差异；`include_dismissed=true` 连已「知道了」的一起返回\n"
        "- `total` 是未处理差异的总数（不受 `limit` 影响）"
    ),
    responses=error_responses("VALIDATION_ERROR", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_account_operator_diffs(
    request: Request,
    include_dismissed: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    return await _box_service(request).list_diffs(
        include_dismissed=include_dismissed, limit=limit
    )


class OperatorDiffDismissRequest(BaseModel):
    """把差异标记为「知道了」：给 ``ids`` 逐条处理，省略表示全部。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"ids": [1, 2]}, {}]},
    )

    ids: list[int] | None = Field(
        default=None, description="差异 id 列表；省略或 null 表示全部"
    )


@router.delete(
    "/operator-diffs",
    summary="标记差异已知",
    description=(
        "把识别差异标记为已知（页面上的「知道了」）。\n\n"
        "- 请求体给 `ids` 逐条处理，省略表示全部\n"
        "- 幂等：重复标记已处理的差异不计入 `dismissed`\n"
        "- 找不到的 id 不报错，只在 `dismissed` 里少计数"
    ),
    responses=error_responses("VALIDATION_ERROR", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def dismiss_account_operator_diffs(
    request: Request,
    payload: OperatorDiffDismissRequest | None = None,
) -> dict[str, Any]:
    ids = payload.ids if payload is not None else None
    dismissed = await _box_service(request).dismiss_diffs(ids)
    return {"dismissed": dismissed}


@router.get(
    "/depot",
    summary="读取仓库",
    description=(
        "仓库识别结果，按物品分类分组展示的只读列表。\n\n"
        "- `category` 与 `query` 都是筛选条件，`total` 是筛选后的总数\n"
        "- 仓库识别只看养成材料页签，龙门币与作战记录不在其中\n"
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
) -> dict[str, Any]:
    return await _depot_service(request).list_items(
        category=category, query=query, limit=limit, offset=offset
    )
