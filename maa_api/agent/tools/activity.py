"""Agent 的当期活动工具：读 PRTS 回答「现在什么活动、这一期有什么关」（规格 §5.6）。

只读、SAFE、不需要设备。国服优先查 PRTS（多个在开活动全部列出），外服或 PRTS 不可用时
退回本机资源包的 M24 推断并如实标注 ``source=resource``（§3.5：降级不得冒充外部来源）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import (
    MAX_STAGES,
    ActivityIndex,
    default_activity_index,
)
from maa_api.services.external.envelope import build_envelope, merge_envelopes

__all__ = ["register_tools"]

#: stageId 前缀的形状（小写字母开头，后跟小写字母/数字/下划线）；
#: 只用于把明显写错的参数挡在服务之前，真正的存在性判断在服务里。
PREFIX_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


class GetActivityStagesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description=(
            "要核对的关卡：代号、stageId、levelId 或中文关卡名都行。"
            "留空则返回当期活动的关卡表。"
        ),
    )
    prefix: str | None = Field(
        default=None,
        min_length=2,
        max_length=32,
        pattern=PREFIX_PATTERN,
        description="指定 stageId 前缀取这一组关卡；用于上面返回的候选里挑一组时。",
    )


def register_tools(registry: ToolRegistry) -> None:
    """注册活动组工具（规格 §5.6）。"""
    registry.register(
        ToolDefinition(
            name="get_activity_stages",
            group="activity",
            risk=ToolRisk.SAFE,
            description=(
                "查当期有哪些活动、各自什么时候结束、这一期有哪些关卡，以及某个关卡代号属于哪一期。"
                "用户提到活动、当期关卡、或问「这关现在能不能打」时先查一次；"
                "提交 Fight 前用它拿到准确的关卡代号。数据来自 PRTS，返回里带出处。"
            ),
            params_model=GetActivityStagesParams,
            handler=get_activity_stages,
            read_only=True,
        )
    )


async def get_activity_stages(
    params: GetActivityStagesParams, context: ToolContext
) -> dict[str, Any]:
    """按参数分派到活动报告或单关查询。"""
    if params.stage is not None:
        return await _find_stage(params.stage, context)
    if params.prefix is not None:
        return await _stages_of_prefix(params.prefix, context)
    return await _report(context)


async def _report(context: ToolContext) -> dict[str, Any]:
    """当期活动报告（PRTS 优先，外服/不可用退回本机）。"""
    service = _activity_service(context)
    report = await service.current(detail="full", client_type=_client_type(context))
    payload = report.as_dict()
    payload.update(
        build_envelope(
            report.sources,
            unavailable=[] if report.activities else ["activities"],
            region_note=report.region_note,
        )
    )
    return payload


async def _find_stage(stage: str, context: ToolContext) -> dict[str, Any]:
    """按 M24 的本机索引查这一关，`is_current` 改成「属于任一在开活动」。"""
    index = _index(context)
    result = index.find_stage(stage)
    if not result["matches"]:
        result["hint"] = (
            "没有匹配的关卡。可以改用 stageId（形如 <前缀>_08）或中文关卡名；"
            "也可以不带参数先看当期活动的关卡表。"
        )
    current = await _current_prefixes(context)
    for match in result["matches"]:
        prefix = _prefix_of(match.get("stage_id"))
        match["is_current"] = prefix in current
        match["activity_name"] = current.get(prefix)
    result["current_activities"] = sorted(set(current.values()))
    result.update(build_envelope())
    return result


async def _stages_of_prefix(prefix: str, context: ToolContext) -> dict[str, Any]:
    """取指定前缀的关卡表；未知前缀给提示而不是空结果。"""
    index = _index(context)
    current = await _current_prefixes(context)
    known: set[str] = set(current)
    try:
        snapshot = index.snapshot().as_dict()
        known.update(
            item["stage_id_prefix"]
            for item in snapshot["alternatives"]
            if item.get("stage_id_prefix")
        )
        if snapshot.get("stage_id_prefix"):
            known.add(snapshot["stage_id_prefix"])
        for match in index.find_stage(prefix)["matches"]:
            candidate = _prefix_of(match.get("stage_id"))
            if candidate:
                known.add(candidate)
    except AppError:
        pass
    if prefix not in known:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"未知的 stageId 前缀：{prefix}。先不带参数查一次，用返回的 stage_id_prefix 或候选前缀",
            {"prefix": prefix, "known": sorted(known)},
        )
    stages = index.stages_of_prefix(prefix)
    return {
        "stage_id_prefix": prefix,
        "current_activities": sorted(set(current.values())),
        "is_current": prefix in current,
        "activity_name": current.get(prefix),
        "stages": [
            {"code": stage.code, "name": stage.name, "stage_id": stage.stage_id}
            for stage in stages[:MAX_STAGES]
        ],
        "stages_total": len(stages),
        **build_envelope(),
    }


async def _current_prefixes(context: ToolContext) -> dict[str, str]:
    """在开活动的 `stageId 前缀 → 活动名`；取不到就返回空表（不报错）。"""
    try:
        report = await _activity_service(context).current(
            detail="full", client_type=_client_type(context)
        )
    except AppError:
        return {}
    mapping: dict[str, str] = {}
    for activity in report.activities:
        if activity.stage_id_prefix:
            mapping.setdefault(activity.stage_id_prefix, activity.name)
    return mapping


def _prefix_of(stage_id: Any) -> str:
    return str(stage_id or "").split("_", 1)[0]


def _index(context: ToolContext) -> ActivityIndex:
    """应用装配的索引优先（测试与将来的多根装配）；缺省用模块级默认门面。"""
    state = getattr(getattr(getattr(context.request, "app", None), "state", None), "activity_index", None)
    if isinstance(state, ActivityIndex):
        return state
    return default_activity_index()


def _activity_service(context: ToolContext) -> Any:
    state = getattr(getattr(context.request, "app", None), "state", None)
    service = getattr(state, "activity_service", None)
    if service is not None:
        return service
    from maa_api.services.game.activities import default_activity_service

    return default_activity_service()


def _client_type(context: ToolContext) -> str | None:
    state = getattr(getattr(context.request, "app", None), "state", None)
    provider = getattr(state, "settings_provider", None)
    if callable(provider):
        try:
            return provider().channel.client_type
        except Exception:  # noqa: BLE001 - 渠道只影响提示语
            return None
    return None


#: `merge_envelopes` 供后续里程碑在活动结果里合并其它来源（M26 起）。
_MERGE_ENVELOPES = merge_envelopes
