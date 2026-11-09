"""产物工具：把白名单组件组成的视图存成 artifact（M90 规格 §4.3.2、§4.5.1）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.artifacts.views import (
    VIEW_MIME,
    ViewSpec,
    view_fields,
    view_kind,
    validate_view,
)
from maa_api.agent.harness.context import require_run
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools._services import v3_service
from maa_api.domain.errors import AppError, ErrorCode

CREATE_VIEW_DESCRIPTION = """要用户一眼看懂一组数字或几项对比，或者要用户在几个选项里做决定、填几个参数时，生成一张视图。组件只能用 heading（标题）、text（文本）、stats（关键数字）、table（表格）、chart（图表）、field（表单字段：select、number、text、toggle）与 submit（提交按钮）；最多 40 个组件、20 个字段、64 KiB。有表单字段时最后一个组件必须是 submit，没有字段时不能有 submit。chart 组件形如 {"type":"chart","caption":"…","chart":{"chart_type":"line|bar|pie","title":"…","categories":[…],"series":[{"name":"…","values":[…]}],"stacked":false,"x_label":"…","y_label":"…","unit":"…","source":"…"}}，chart 内的字段与 send_chart 同名但平铺在 chart 下，caption 在组件层；总点数不超过 600，序列化不超过 24 KiB。用户提交后，答案会作为一条【表单提交】消息发给你；表单答案不等于确认。视图不代替汇报，本轮照常以 report 收尾。"""


class CreateViewParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: dict[str, Any] = Field(
        description="视图定义，组件只能是 heading、text、stats、table、chart、field、submit"
    )


async def create_view(params: CreateViewParams, ctx: ToolContext) -> dict[str, Any]:
    """校验视图，按当前运行的工单与会话写一份 artifact，返回 id 与种类。"""
    run = require_run(kinds={"worker"}, message="create_view 只能在 Worker 里调用")
    if not run.ticket_id:
        raise AppError(ErrorCode.INVALID_PARAMETER, "只能在工单里生成视图")
    try:
        spec: ViewSpec = validate_view(params.view)
    except ValueError as exc:
        errors = getattr(exc, "errors", [])
        head = "；".join(
            f"{item['path']}：{item['message']}" if item["path"] else item["message"]
            for item in errors[:3]
        )
        raise AppError(
            ErrorCode.TOOL_ARGS_INVALID,
            f"视图不合法：{head}",
            {"errors": errors[:20]},
        ) from None
    service = v3_service(run, ctx, "artifact_service", "产物服务")
    body = spec.model_dump_json(exclude_none=True).encode("utf-8")
    artifact = await service.put(
        kind=view_kind(spec),
        title=spec.title,
        body=body,
        mime=VIEW_MIME,
        session_id=run.session_id,
        ticket_id=run.ticket_id,
        run_id=run.run_id,
        meta={"view_version": 1, "submissions": [], "last_submitted_at": None},
    )
    return {
        "artifact_id": str(artifact.id),
        "kind": view_kind(spec),
        "title": spec.title,
        "field_count": len(view_fields(spec)),
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="create_view",
            group="artifacts",
            risk=ToolRisk.SAFE,
            description=CREATE_VIEW_DESCRIPTION,
            params_model=CreateViewParams,
            handler=create_view,
            read_only=False,
            refetchable=False,
            device="none",
            untrusted_output=False,
            rest_visible=False,
        )
    )
