"""代理定义端点（规格 §4.3.1）：内置定义列表与单个定义的完整内容。

两个只读端点都从 ``app.state.agent_spec_registry`` 取启动时加载好的注册表；注册表
尚未装配（进程启动中）返回 503 ``SERVICE_UNAVAILABLE``。响应必须走 Pydantic 模型，
OpenAPI 里才有 ``SpecList`` / ``SpecDetail`` 两个 schema。列表按 ``name`` 排序，
详情在列表字段之外再给 ``deferred_groups``、``device`` 等 8 个字段与正文提示词。

查不到的名字由 ``AgentSpecRegistry.get`` 抛 404 ``AGENT_SPEC_NOT_FOUND``，message 里
带上可用名字的去处；路由不再重复判断。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel

from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.specs.schema import (
    AgentKind,
    AgentSpec,
    DeviceLevel,
    ModelRoleName,
    RiskCeiling,
)
from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["SpecBudget", "SpecDetail", "SpecList", "SpecSummary", "router"]

router = APIRouter(prefix="/api/agent/specs", tags=["agent"])


class SpecSummary(BaseModel):
    """列表项：一个代理定义的身份、种类、工具与版本。"""

    name: str
    title: str
    kind: AgentKind
    description: str
    model_role: ModelRoleName
    tools: list[str]
    delegates: list[str]
    version: int
    spec_version: str


class SpecList(BaseModel):
    """列表响应：``items`` 按 ``name`` 排序。"""

    items: list[SpecSummary]


class SpecBudget(BaseModel):
    """单次运行的预算上限。"""

    max_cost_per_run: float


class SpecDetail(SpecSummary):
    """详情：列表字段之外再加定义文件里其余 8 个字段与正文提示词。"""

    deferred_groups: list[str]
    device: DeviceLevel
    risk_ceiling: RiskCeiling
    max_steps: int
    skills: list[str]
    output_schema: str | None
    budget: SpecBudget
    body: str


def _registry(request: Request) -> AgentSpecRegistry:
    registry = getattr(request.app.state, "agent_spec_registry", None)
    if registry is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "代理定义还在加载，请稍后重试")
    return registry


def _summary(spec: AgentSpec) -> SpecSummary:
    return SpecSummary(
        name=spec.name,
        title=spec.title,
        kind=spec.kind,
        description=spec.description,
        model_role=spec.model_role,
        tools=list(spec.tools),
        delegates=list(spec.delegates),
        version=spec.version,
        spec_version=spec.spec_version,
    )


def _detail(spec: AgentSpec) -> SpecDetail:
    return SpecDetail(
        **_summary(spec).model_dump(),
        deferred_groups=list(spec.deferred_groups),
        device=spec.device,
        risk_ceiling=spec.risk_ceiling,
        max_steps=spec.max_steps,
        skills=list(spec.skills),
        output_schema=spec.output_schema,
        budget=SpecBudget(max_cost_per_run=spec.budget.max_cost_per_run),
        body=spec.body,
    )


@router.get(
    "",
    response_model=SpecList,
    summary="读取代理定义列表",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def list_specs(request: Request) -> SpecList:
    return SpecList(items=[_summary(spec) for spec in _registry(request).list()])


@router.get(
    "/{name}",
    response_model=SpecDetail,
    summary="读取代理定义详情",
    responses=error_responses(
        "AGENT_SPEC_NOT_FOUND", "SERVICE_UNAVAILABLE", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def get_spec(
    request: Request,
    name: str = Path(description="代理定义名字，可用的名字见列表端点"),
) -> SpecDetail:
    return _detail(_registry(request).get(name))
