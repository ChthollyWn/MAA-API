"""事件种类登记表（M81 规格 §4.2、08 §2.2/§2.4）。

一条事件是 ``agent_trigger`` 的一行，``kind`` 写成 ``<领域>.<动作>``。只有本表
登记过的种类能入表；M82、M83、M85 往 :data:`KNOWN_KINDS` 里加自己的种类。

三个使用方：

- ``TriggerService.publish`` 先用 :func:`validate_event` 做第一级程序准入；
- 总控收件箱、事件列表、研判事件卡与推送标题用 :func:`summarize` 取固定格式摘要；
- 规则没有建工单/写观察记录时，用 :func:`needs_attention` 判断要不要交总控。

摘要模板即 08 §2.4 的表，逐字照抄；缺值写「未知」，整句不超过 200 字。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from maa_api.domain.enums import EventSource

__all__ = [
    "ATTENTION_KINDS",
    "EventKind",
    "KNOWN_KINDS",
    "MAX_PAYLOAD_BYTES",
    "MAX_SUMMARY_CHARS",
    "SUMMARIZE_UNKNOWN",
    "needs_attention",
    "summarize",
    "validate_event",
]

#: 08 §2.1：payload 序列化后不超过 16 KiB。
MAX_PAYLOAD_BYTES = 16 * 1024
#: 08 §2.4：摘要整句不超过 200 字。
MAX_SUMMARY_CHARS = 200
#: 摘要里缺值时的占位（08 §2.4）。
SUMMARIZE_UNKNOWN = "未知"


@dataclass(frozen=True, slots=True)
class EventKind:
    """一个登记过的事件种类。"""

    #: ``"pipeline.failed"``
    kind: str
    #: 允许的 :class:`EventSource` 取值；``im.message`` 是四个 ``im_*``。
    sources: frozenset[str]
    #: ``"pipeline:"``，与 03 §15 的话题键前缀一致。
    thread_prefix: str
    #: 08 §2.4 的摘要模板，用 ``str.format_map`` 填 payload 字段。
    summary: str
    #: 无工单处理时是否交总控；M81 有 ``core.failed``、``device.unavailable``，
    #: M82 追加 ``goal.completed``、``goal.deadline_passed`` 恒为真。
    attention: Callable[[Mapping[str, Any]], bool] | None = None


def _sources(*values: EventSource) -> frozenset[str]:
    """把枚举成员折成事件表里的字符串集合。"""
    return frozenset(value.value for value in values)


_PIPELINE = _sources(EventSource.PIPELINE)
_CORE = _sources(EventSource.CORE)
_DEVICE = _sources(EventSource.DEVICE)
_SCHEDULE = _sources(EventSource.SCHEDULE)
_SYSTEM = _sources(EventSource.SYSTEM)
_GOAL = _sources(EventSource.GOAL)
#: IM 消息来自四个平台；08 §2.2 用 ``im_*`` 表示这一族来源。
_IM = _sources(
    EventSource.IM_TELEGRAM,
    EventSource.IM_FEISHU,
    EventSource.IM_DINGTALK,
    EventSource.IM_QQ,
)


def _always(_payload: Mapping[str, Any]) -> bool:
    return True


#: M81 登记的 12 种事件、M82 追加的 3 种与 M85 追加的 1 种，顺序照 08 §2.2 的表。
KNOWN_KINDS: dict[str, EventKind] = {
    "pipeline.completed": EventKind(
        kind="pipeline.completed",
        sources=_PIPELINE,
        thread_prefix="pipeline:",
        summary="流水线「{title}」已完成",
    ),
    "pipeline.failed": EventKind(
        kind="pipeline.failed",
        sources=_PIPELINE,
        thread_prefix="pipeline:",
        summary="流水线「{title}」失败：{error_message}",
    ),
    "pipeline.cancelled": EventKind(
        kind="pipeline.cancelled",
        sources=_PIPELINE,
        thread_prefix="pipeline:",
        summary="流水线「{title}」已停止",
    ),
    "pipeline.core_crash_linked": EventKind(
        kind="pipeline.core_crash_linked",
        sources=_PIPELINE,
        thread_prefix="pipeline:",
        summary="流水线 {pipeline_id} 的失败归因到内核第 {core_generation} 代崩溃",
    ),
    "core.crashed": EventKind(
        kind="core.crashed",
        sources=_CORE,
        thread_prefix="core:",
        summary="内核第 {generation} 代崩溃（{reason}），没有流水线在跑",
    ),
    "core.failed": EventKind(
        kind="core.failed",
        sources=_CORE,
        thread_prefix="core:",
        summary="内核自动重启次数用完，需要手动启动",
        attention=_always,
    ),
    "device.connected": EventKind(
        kind="device.connected",
        sources=_DEVICE,
        thread_prefix="device:",
        summary="设备已连接（{address}）",
    ),
    "device.disconnected": EventKind(
        kind="device.disconnected",
        sources=_DEVICE,
        thread_prefix="device:",
        summary="设备连接断开（{address}）",
    ),
    "device.unavailable": EventKind(
        kind="device.unavailable",
        sources=_DEVICE,
        thread_prefix="device:",
        summary="设备重连 {retry_attempt} 次都失败，暂时不可用",
        attention=_always,
    ),
    "schedule.fired": EventKind(
        kind="schedule.fired",
        sources=_SCHEDULE,
        thread_prefix="schedule:",
        summary="定时规则 {rule_id} 到点",
    ),
    "system.update_available": EventKind(
        kind="system.update_available",
        sources=_SYSTEM,
        thread_prefix="system:update_available",
        summary="{target} 有新版本 {latest}（当前 {current}）",
    ),
    "system.sanity_full": EventKind(
        kind="system.sanity_full",
        sources=_SYSTEM,
        thread_prefix="system:sanity",
        summary="理智估计已满（{estimated}/{max_sanity}）",
    ),
    # M82：目标只记录复盘、完成与过截止；后两种无论如何都交总控（08 §2.2）。
    "goal.review_due": EventKind(
        kind="goal.review_due",
        sources=_GOAL,
        thread_prefix="goal:",
        summary="目标「{title}」到了复盘时间",
    ),
    "goal.completed": EventKind(
        kind="goal.completed",
        sources=_GOAL,
        thread_prefix="goal:",
        summary="目标「{title}」已完成",
        attention=_always,
    ),
    "goal.deadline_passed": EventKind(
        kind="goal.deadline_passed",
        sources=_GOAL,
        thread_prefix="goal:",
        summary="目标「{title}」过了截止还没完成",
        attention=_always,
    ),
    # M85：IM 消息是用户的原话，不是需要研判的事件；``attention=None`` 因为它走
    # 08 §2.3 的第 3 步时按 user_message 入收件箱，由事件总线单独处理（见 bus）。
    "im.message": EventKind(
        kind="im.message",
        sources=_IM,
        thread_prefix="im:",
        summary="IM 消息：{text}",
    ),
}

#: 「交总控」集合：attention 始终为真的已登记种类（08 §2.3；M82/M83/M85 各自追加）。
ATTENTION_KINDS: frozenset[str] = frozenset(
    kind for kind, entry in KNOWN_KINDS.items() if entry.attention is not None
)


def _source_value(source: EventSource | str) -> str:
    """``EventSource`` 与字符串都归一成事件表里的取值。"""
    if isinstance(source, EventSource):
        return source.value
    return str(source)


def validate_event(
    source: str, kind: str, thread_key: str, payload: Mapping[str, Any]
) -> str | None:
    """第一级程序准入（08 §4.2）。

    不合法返回原因：``unknown_kind``、``source_mismatch``、
    ``thread_prefix_mismatch``、``payload_too_large``；合法返回 ``None``。
    """
    entry = KNOWN_KINDS.get(kind)
    if entry is None:
        return "unknown_kind"
    if _source_value(source) not in entry.sources:
        return "source_mismatch"
    if not thread_key.startswith(entry.thread_prefix):
        return "thread_prefix_mismatch"
    if len(_payload_json(payload).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return "payload_too_large"
    return None


def _payload_json(payload: Mapping[str, Any]) -> str:
    """按写进事件的形状序列化 payload；序列化失败按超限处理由调用方兜底。"""
    try:
        return json.dumps(dict(payload), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return "{}"


class _MissingValueMap(dict[str, Any]):
    """``format_map`` 的填值表：缺字段与 ``None`` 都写「未知」。"""

    def __missing__(self, key: str) -> str:
        return SUMMARIZE_UNKNOWN


def summarize(kind: str, payload: Mapping[str, Any]) -> str:
    """按 08 §2.4 的模板生成一句摘要；缺值写「未知」，截到 200 字。"""
    entry = KNOWN_KINDS.get(kind)
    if entry is None:
        return kind[:MAX_SUMMARY_CHARS]
    values = _MissingValueMap()
    for key, value in payload.items():
        values[str(key)] = SUMMARIZE_UNKNOWN if value is None else value
    try:
        text = entry.summary.format_map(values)
    except (KeyError, IndexError, ValueError):
        # 模板里出现非法占位符（例如 ``{}``）时退回种类名，不让坏模板打断处理。
        text = kind
    return text[:MAX_SUMMARY_CHARS]


def needs_attention(kind: str, payload: Mapping[str, Any]) -> bool:
    """无工单处理时是否交总控；未登记的种类与未声明 attention 的种类都不交。"""
    entry = KNOWN_KINDS.get(kind)
    if entry is None or entry.attention is None:
        return False
    return bool(entry.attention(payload))
