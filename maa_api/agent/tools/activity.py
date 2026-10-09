"""Agent 的当期活动工具：读本机活动资源回答「现在什么活动、这一期有什么关」。

只读、SAFE、不需要设备。数据每次现读资源层（见 ``maa_api.services.activity_index``），
所以资源更新之后自动跟上新活动，不存在「知识库里写的活动已经过期」这种情况。
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
    """注册活动组工具（规格 §4.2）。"""
    registry.register(
        ToolDefinition(
            name="get_activity_stages",
            group="activity",
            risk=ToolRisk.SAFE,
            description=(
                "查当期活动叫什么、这一期有哪些关卡，以及某个关卡代号属于哪一期。"
                "用户提到活动、当期关卡、或问「这关现在能不能打」时先查一次；"
                "提交 Fight 前用它拿到准确的关卡代号。"
            ),
            params_model=GetActivityStagesParams,
            handler=get_activity_stages,
            read_only=True,
        )
    )


async def get_activity_stages(
    params: GetActivityStagesParams, context: ToolContext
) -> dict[str, Any]:
    """按参数分派到活动快照或单关查询。"""
    index = _index(context)
    if params.stage is not None:
        result = index.find_stage(params.stage)
        if not result["matches"]:
            result["hint"] = (
                "没有匹配的关卡。可以改用 stageId（形如 <前缀>_08）或中文关卡名；"
                "也可以不带参数先看当期活动的关卡表。"
            )
        return result
    if params.prefix is not None:
        return _stages_of_prefix(index, params.prefix)
    return index.snapshot().as_dict()


def _stages_of_prefix(index: ActivityIndex, prefix: str) -> dict[str, Any]:
    """取指定前缀的关卡表；未知前缀给提示而不是空结果。"""
    snapshot = index.snapshot()
    # ``known`` 只列模型**在返回里能看到**的前缀：``as_dict()`` 会把 ``alternatives``
    # 截断，若这里用未截断的列表，模型会看到「未知前缀」里出现自己没见过的名字。
    visible = snapshot.as_dict()["alternatives"]
    known = {
        *(item["stage_id_prefix"] for item in visible),
        *([snapshot.stage_id_prefix] if snapshot.stage_id_prefix else []),
    }
    if prefix not in known:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"未知的 stageId 前缀：{prefix}。先不带参数查一次，用返回的 stage_id_prefix 或候选前缀",
            {"prefix": prefix, "known": sorted(known)},
        )
    stages = index.stages_of_prefix(prefix)
    return {
        "stage_id_prefix": prefix,
        "current_activity": snapshot.name,
        "is_current": prefix == snapshot.stage_id_prefix,
        "stages": [
            {"code": stage.code, "name": stage.name, "stage_id": stage.stage_id}
            for stage in stages[:MAX_STAGES]
        ],
        "stages_total": len(stages),
        "source": snapshot.source,
    }


def _index(context: ToolContext) -> ActivityIndex:
    """应用装配的索引优先（测试与将来的多根装配）；缺省用模块级默认门面。"""
    state = getattr(getattr(getattr(context.request, "app", None), "state", None), "activity_index", None)
    if isinstance(state, ActivityIndex):
        return state
    return default_activity_index()
