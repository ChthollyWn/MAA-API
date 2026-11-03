"""模型档案、角色与成本端点（M67 规格 §4.3.1）。

八个端点全部挂在 ``/api/agent`` 下并沿用 ``agent`` tag（``operationId`` 是
``agent_*``）：

* 档案 CRUD 与 ``/{name}/probe``：读写 :class:`ModelProfileService`，``default`` 由
  ``llm.*`` 设置合成且只读；被角色引用的档案不能删除或停用，服务层抛出的
  ``MODEL_PROFILE_*`` 错误由路由原样透传。
* ``GET /api/agent/model-roles``：:class:`ModelRouter` 的九个角色各一行，顺序与
  :class:`ModelRole` 一致。
* ``GET /api/agent/costs``：:class:`CostLedger.summarize` 的查询入口。区间与分组
  口径的边界在路由层判断（400 ``INVALID_PARAMETER``），时间先归一成 naive UTC。

响应一律走 Pydantic 模型（档案视图、探查结果、角色列表、成本汇总），服务尚未装配
（进程启动中、或测试只装了路由）返回 503 ``SERVICE_UNAVAILABLE``。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel

from maa_api.agent.llm.cost import (
    GROUP_BY_CHOICES,
    CostLedger,
    CostSummaryView,
)
from maa_api.agent.llm.profiles import (
    PROFILE_NAME_PATTERN,
    ModelProfileCreate,
    ModelProfileList,
    ModelProfileService,
    ModelProfileUpdate,
    ModelProfileView,
    ProbeResultView,
)
from maa_api.agent.llm.roles import ModelRouter
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["ModelRoleItem", "ModelRolesView", "router"]

router = APIRouter(prefix="/api/agent", tags=["agent"])

#: ``until - since`` 的上限（规格 §4.3.1：跨度超过 366 天是 400）。
MAX_COST_RANGE = timedelta(days=366)
#: 币种查询参数的形状；不合法与 ``group_by`` 一样走 400 而不是框架的 422。
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
#: 不传时间时看最近 7 天（规格 §4.3.1）。
DEFAULT_COST_DAYS = 7


class ModelRoleItem(BaseModel):
    """一个角色当前生效的设置值、解析出的模型与来源。"""

    role: str
    profile: str | None
    model: str | None
    source: Literal["setting", "default"]


class ModelRolesView(BaseModel):
    """``GET /api/agent/model-roles`` 的响应体；``items`` 按 ``ModelRole`` 顺序。"""

    items: list[ModelRoleItem]


def _profiles(request: Request) -> ModelProfileService:
    """取档案服务；装配层还没设置时返回 503。"""
    service = getattr(request.app.state, "model_profile_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "模型档案还在启动中，请稍后重试")
    return service


def _model_router(request: Request) -> ModelRouter:
    """取角色路由；装配层还没设置时返回 503。"""
    model_router = getattr(request.app.state, "model_router", None)
    if model_router is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "模型档案还在启动中，请稍后重试")
    return model_router


def _ledger(request: Request) -> CostLedger:
    """取成本账本；装配层还没设置时返回 503。"""
    ledger = getattr(request.app.state, "cost_ledger", None)
    if ledger is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "成本账本还在启动中，请稍后重试")
    return ledger


def _naive_utc(value: datetime) -> datetime:
    """带时区的时间换算成 naive UTC；不带的按 UTC 理解（规格 §4.3.1）。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@router.get(
    "/model-profiles",
    response_model=ModelProfileList,
    summary="读取模型档案列表",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_model_profiles(request: Request) -> ModelProfileList:
    """``default`` 在最前，其余按 ``name`` 升序。"""
    return ModelProfileList(items=await _profiles(request).list_views())


@router.post(
    "/model-profiles",
    response_model=ModelProfileView,
    status_code=status.HTTP_201_CREATED,
    summary="新建模型档案",
    responses=error_responses(
        "MODEL_PROFILE_EXISTS",
        "VALIDATION_ERROR",
        "LLM_ENCRYPTION_KEY_INVALID",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def create_model_profile(
    payload: ModelProfileCreate, request: Request, response: Response
) -> ModelProfileView:
    """成功时带 ``Location`` 指向新档案。"""
    view = await _profiles(request).create(payload)
    response.headers["Location"] = f"/api/agent/model-profiles/{view.name}"
    return view


@router.get(
    "/model-profiles/{name}",
    response_model=ModelProfileView,
    summary="读取模型档案",
    responses=error_responses(
        "MODEL_PROFILE_NOT_FOUND", "VALIDATION_ERROR", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_model_profile(
    request: Request,
    name: str = Path(description="模型档案名字", pattern=PROFILE_NAME_PATTERN),
) -> ModelProfileView:
    return await _profiles(request).get_view(name)


@router.put(
    "/model-profiles/{name}",
    response_model=ModelProfileView,
    summary="修改模型档案",
    responses=error_responses(
        "MODEL_PROFILE_NOT_FOUND",
        "MODEL_PROFILE_READONLY",
        "MODEL_PROFILE_IN_USE",
        "VALIDATION_ERROR",
        "LLM_ENCRYPTION_KEY_INVALID",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def update_model_profile(
    payload: ModelProfileUpdate,
    request: Request,
    name: str = Path(description="模型档案名字", pattern=PROFILE_NAME_PATTERN),
) -> ModelProfileView:
    """字段全部可选；``api_key="***"`` 表示不改，``""`` 表示清除。"""
    return await _profiles(request).update(name, payload)


@router.delete(
    "/model-profiles/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除模型档案",
    responses=error_responses(
        "MODEL_PROFILE_NOT_FOUND",
        "MODEL_PROFILE_READONLY",
        "MODEL_PROFILE_IN_USE",
        "VALIDATION_ERROR",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def delete_model_profile(
    request: Request,
    name: str = Path(description="模型档案名字", pattern=PROFILE_NAME_PATTERN),
) -> Response:
    """被角色引用时返回 409 ``MODEL_PROFILE_IN_USE``，``default`` 不可删除。"""
    await _profiles(request).delete(name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/model-profiles/{name}/probe",
    response_model=ProbeResultView,
    summary="探查模型档案的能力",
    responses=error_responses(
        "MODEL_PROFILE_NOT_FOUND",
        "INVALID_PARAMETER",
        "LLM_NOT_CONFIGURED",
        "LLM_ENCRYPTION_KEY_INVALID",
        "LLM_REQUEST_FAILED",
        "LLM_TIMEOUT",
        "LLM_RATE_LIMITED",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def probe_model_profile(
    request: Request,
    name: str = Path(description="模型档案名字", pattern=PROFILE_NAME_PATTERN),
) -> ProbeResultView:
    """依次探查工具调用、视觉、JSON Schema 与尾部缓存位置，并写回档案。"""
    result = await _profiles(request).probe(name)
    return ProbeResultView(
        supports_vision=result.supports_vision,
        supports_tools=result.supports_tools,
        supports_json_schema=result.supports_json_schema,
        latency_ms=result.latency_ms,
    )


@router.get(
    "/model-roles",
    response_model=ModelRolesView,
    summary="读取角色到模型档案的绑定",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_model_roles(request: Request) -> ModelRolesView:
    """九个角色各一行；``embedding`` 未设置时 ``profile`` 与 ``model`` 都是 null。"""
    bindings = await _model_router(request).list_roles()
    return ModelRolesView(
        items=[
            ModelRoleItem(
                role=binding.role,
                profile=binding.profile,
                model=binding.model,
                source=binding.source,
            )
            for binding in bindings
        ]
    )


@router.get(
    "/costs",
    response_model=CostSummaryView,
    summary="汇总模型调用成本",
    responses=error_responses(
        "INVALID_PARAMETER", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def summarize_costs(
    request: Request,
    since: datetime | None = Query(
        None, description="起始时间（含），ISO 8601；缺省为 7 天前"
    ),
    until: datetime | None = Query(
        None, description="结束时间（不含），ISO 8601；缺省为现在"
    ),
    group_by: str = Query(
        "day", description="分组口径：day / agent / model / role"
    ),
    currency: str | None = Query(
        None, description="三位大写币种；缺省取区间内账本行最多的币种"
    ),
) -> CostSummaryView:
    """``since <= created_at < until``；默认按服务器本地日期分组。"""
    if group_by not in GROUP_BY_CHOICES:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"group_by 必须是 {' / '.join(GROUP_BY_CHOICES)} 之一",
        )
    if currency is not None and CURRENCY_PATTERN.fullmatch(currency) is None:
        raise AppError(ErrorCode.INVALID_PARAMETER, "currency 必须是三个大写字母")

    now = datetime.now(UTC)
    until_value = _naive_utc(until) if until is not None else _naive_utc(now)
    since_value = (
        _naive_utc(since)
        if since is not None
        else _naive_utc(now) - timedelta(days=DEFAULT_COST_DAYS)
    )
    if since_value >= until_value:
        raise AppError(ErrorCode.INVALID_PARAMETER, "since 必须早于 until")
    if until_value - since_value > MAX_COST_RANGE:
        raise AppError(ErrorCode.INVALID_PARAMETER, "查询区间不能超过 366 天")

    return await _ledger(request).summarize(
        since=since_value,
        until=until_value,
        group_by=group_by,
        currency=currency,
    )
