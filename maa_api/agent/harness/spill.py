"""工具结果外置：超长输出存成产物，上下文里只留指针（M75 规格 §4.5、v3 06 §6.1）。

外置只改中间件的返回值：工具函数本身仍返回完整结果，内层 ``PolicyMiddleware`` 在
替换之前已经把全文写进审计。``content``（落库的 tool 消息）与 ``result``（SSE 的
``tool_result``）必须一起换，否则前端仍会收到全文。

只有代理定义里带 ``read_artifact`` 的才外置；总控与子代理没有这个工具，保持 v2 的
完整输出行为。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from maa_api.agent.harness.context import RunContext, ToolCall, ToolOutcome
from maa_api.agent.harness.middleware import CallNext, Middleware
from maa_api.settings import get_settings

__all__ = [
    "DEFAULT_SPILL_THRESHOLD_CHARS",
    "NOT_SPILLED_TOOLS",
    "SPILL_HINT",
    "SpillMiddleware",
    "resolve_spill_threshold",
]

logger = logging.getLogger(__name__)

#: ``context.spill_threshold_chars`` 的默认值；设置还没加这个键时也用它。
DEFAULT_SPILL_THRESHOLD_CHARS = 8000
#: 指针里的续读提示（06 §6.1 逐字）。
SPILL_HINT = "完整输出已存为产物，用 read_artifact 从 next_offset 续读"
#: 预览的前后字符数（06 §6.1）：前 3000 + 后 1000。
PREVIEW_HEAD_CHARS = 3000
PREVIEW_TAIL_CHARS = 1000
#: 指针里 ``next_offset`` 的固定起点：预览覆盖到前 3000 字。
NEXT_OFFSET_AFTER_PREVIEW = PREVIEW_HEAD_CHARS

#: 自带分页/输出预算、图像/媒体与代理内部工具；与 06 §6.1 的名单一致。
NOT_SPILLED_TOOLS: frozenset[str] = frozenset(
    {
        "get_operator_box",
        "get_logs",
        "read_file",
        "search_code",
        "list_files",
        "read_artifact",
        "report",
        "delegate",
        "ask_user",
        "update_plan",
        "write_note",
        "get_screenshot",
        "send_screenshot",
        "annotate_screenshot",
        "send_item_icons",
        "send_stage_map",
        "send_chart",
    }
)
#: 结果里出现这些键就当作图像/媒体结果，不外置。
_IMAGE_KEYS = frozenset({"media", "image", "images", "image_base64", "image_url"})


def resolve_spill_threshold(provider: Callable[[], Any] | None = None) -> int:
    """读 ``context.spill_threshold_chars``；设置对象还没有这个键时用默认值。"""
    try:
        settings = provider() if provider is not None else get_settings()
        context = getattr(settings, "context", None)
        value = getattr(context, "spill_threshold_chars", None)
        if isinstance(value, int) and value > 0:
            return value
    except Exception:  # noqa: BLE001 - 读设置失败不能把一次成功的工具调用变成失败
        logger.warning("读取外置阈值失败，使用默认值", exc_info=True)
    return DEFAULT_SPILL_THRESHOLD_CHARS


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _has_image_payload(result: Any) -> bool:
    if isinstance(result, Mapping):
        return bool(_IMAGE_KEYS.intersection(result))
    return False


def _preview(text: str) -> str:
    """前 3000 字 + 省略说明 + 后 1000 字；文本不够长时原样返回。"""
    if len(text) <= PREVIEW_HEAD_CHARS + PREVIEW_TAIL_CHARS:
        return text
    omitted = len(text) - PREVIEW_HEAD_CHARS - PREVIEW_TAIL_CHARS
    return (
        f"{text[:PREVIEW_HEAD_CHARS]}\n"
        f"[中间省略 {omitted} 字]\n"
        f"{text[-PREVIEW_TAIL_CHARS:]}"
    )


def _pointer(*, tool: str, artifact_id: str, text: str) -> dict[str, Any]:
    return {
        "spilled": True,
        "artifact_id": artifact_id,
        "tool": tool,
        "total_chars": len(text),
        "preview": _preview(text),
        "next_offset": NEXT_OFFSET_AFTER_PREVIEW,
        "hint": SPILL_HINT,
    }


def _artifact_service(ctx: RunContext) -> Any:
    """从运行的应用取产物服务；还没装配时返回 ``None``（不外置）。"""
    state = getattr(ctx.app, "state", None)
    if state is None:
        state = getattr(getattr(ctx.request, "app", None), "state", None)
    return getattr(state, "artifact_service", None)


class SpillMiddleware(Middleware):
    """把超过阈值的成功工具结果换成产物指针；``wrap_tool`` 里替换。"""

    name = "spill"

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Any] | None = None,
        threshold_chars: int | None = None,
    ) -> None:
        self._settings_provider = settings_provider
        self._threshold_chars = threshold_chars

    @property
    def threshold_chars(self) -> int:
        if self._threshold_chars is not None:
            return self._threshold_chars
        return resolve_spill_threshold(self._settings_provider)

    async def wrap_tool(
        self, ctx: RunContext, call: ToolCall, call_next: CallNext
    ) -> ToolOutcome:
        outcome = await call_next(call)
        if outcome.status != "ok" or outcome.result is None:
            return outcome
        if call.name in NOT_SPILLED_TOOLS or _has_image_payload(outcome.result):
            return outcome
        if "read_artifact" not in getattr(ctx.spec, "tools", ()):
            return outcome
        text = _json_text(outcome.result)
        if len(text) <= self.threshold_chars:
            return outcome
        service = _artifact_service(ctx)
        if service is None:
            return outcome
        try:
            artifact = await service.put(
                kind="spill",
                title=f"{call.name} 的输出",
                body=text.encode("utf-8"),
                mime="application/json",
                session_id=ctx.session_id,
                ticket_id=ctx.ticket_id,
                run_id=ctx.run_id,
                meta={"tool": call.name, "total_chars": len(text)},
            )
        except Exception:  # noqa: BLE001 - 外置失败不能盖过已经成功的工具调用
            logger.warning(
                "工具输出外置失败，保留原文：tool=%s run=%s",
                call.name,
                ctx.run_id,
                exc_info=True,
            )
            return outcome
        pointer = _pointer(
            tool=call.name, artifact_id=str(artifact.id), text=text
        )
        # 审计在内层、替换之前完成（测试锁顺序）；这里只改给模型与 SSE 的副本。
        outcome.result = pointer
        outcome.content = _json_text(pointer)
        return outcome
