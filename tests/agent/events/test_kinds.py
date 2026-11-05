"""事件种类登记表与第一级准入（M81 规格 §4.2、08 §2.2/§2.4、03 §15）。

覆盖 §6 的三条：前缀/来源与 03 §15 的表一致；四种不合法原因；摘要缺字段写「未知」
且不超过 200 字。
"""

from __future__ import annotations

import re

from maa_api.agent.events.kinds import (
    ATTENTION_KINDS,
    KNOWN_KINDS,
    MAX_PAYLOAD_BYTES,
    MAX_SUMMARY_CHARS,
    needs_attention,
    summarize,
    validate_event,
)
from maa_api.domain.enums import EventSource

#: 08 §2.2 的 15 种事件：kind → (source, 03 §15 前缀, 08 §2.4 摘要模板)。
EXPECTED = [
    (
        "pipeline.completed",
        "pipeline",
        "pipeline:",
        "流水线「{title}」已完成",
    ),
    (
        "pipeline.failed",
        "pipeline",
        "pipeline:",
        "流水线「{title}」失败：{error_message}",
    ),
    (
        "pipeline.cancelled",
        "pipeline",
        "pipeline:",
        "流水线「{title}」已停止",
    ),
    (
        "pipeline.core_crash_linked",
        "pipeline",
        "pipeline:",
        "流水线 {pipeline_id} 的失败归因到内核第 {core_generation} 代崩溃",
    ),
    (
        "core.crashed",
        "core",
        "core:",
        "内核第 {generation} 代崩溃（{reason}），没有流水线在跑",
    ),
    (
        "core.failed",
        "core",
        "core:",
        "内核自动重启次数用完，需要手动启动",
    ),
    ("device.connected", "device", "device:", "设备已连接（{address}）"),
    ("device.disconnected", "device", "device:", "设备连接断开（{address}）"),
    (
        "device.unavailable",
        "device",
        "device:",
        "设备重连 {retry_attempt} 次都失败，暂时不可用",
    ),
    ("schedule.fired", "schedule", "schedule:", "定时规则 {rule_id} 到点"),
    (
        "system.update_available",
        "system",
        "system:update_available",
        "{target} 有新版本 {latest}（当前 {current}）",
    ),
    (
        "system.sanity_full",
        "system",
        "system:sanity",
        "理智估计已满（{estimated}/{max_sanity}）",
    ),
    (
        "goal.review_due",
        "goal",
        "goal:",
        "目标「{title}」到了复盘时间",
    ),
    (
        "goal.completed",
        "goal",
        "goal:",
        "目标「{title}」已完成",
    ),
    (
        "goal.deadline_passed",
        "goal",
        "goal:",
        "目标「{title}」过了截止还没完成",
    ),
]


def test_every_known_kind_matches_thread_prefix_table():
    assert list(KNOWN_KINDS) == [kind for kind, *_ in EXPECTED]
    assert len(KNOWN_KINDS) == 15

    event_sources = {source.value for source in EventSource}
    for kind, source, prefix, template in EXPECTED:
        entry = KNOWN_KINDS[kind]
        assert entry.kind == kind
        assert entry.sources == frozenset({source})
        assert source in event_sources
        # 03 §15：前缀属于该来源的领域，且 kind 是 <领域>.<动作>。
        assert entry.thread_prefix.startswith(f"{source}:")
        assert prefix == entry.thread_prefix
        assert re.fullmatch(r"[a-z]+\.[a-z_]+", kind)
        assert len(kind) <= 48
        assert entry.summary == template


def test_validate_rejects_unknown_kind_mismatch_and_oversize():
    assert (
        validate_event("pipeline", "pipeline.unknown", "pipeline:1", {})
        == "unknown_kind"
    )
    assert (
        validate_event("core", "pipeline.failed", "pipeline:1", {})
        == "source_mismatch"
    )
    assert (
        validate_event("pipeline", "pipeline.failed", "core:default", {})
        == "thread_prefix_mismatch"
    )
    oversized = {"blob": "x" * (MAX_PAYLOAD_BYTES + 64)}
    assert (
        validate_event("pipeline", "pipeline.failed", "pipeline:1", oversized)
        == "payload_too_large"
    )
    assert (
        validate_event(
            "pipeline", "pipeline.failed", "pipeline:1", {"title": "日常", "tasks": []}
        )
        is None
    )
    assert (
        validate_event("system", "system.sanity_full", "system:sanity", {}) is None
    )
    # EventSource 成员与字符串等价。
    assert (
        validate_event(
            EventSource.DEVICE, "device.connected", "device:default", {}
        )
        is None
    )


def test_summary_fills_missing_fields_with_unknown():
    assert summarize("pipeline.failed", {}) == "流水线「未知」失败：未知"
    assert summarize("pipeline.failed", {"title": None}) == "流水线「未知」失败：未知"
    assert (
        summarize("system.sanity_full", {"estimated": 135, "max_sanity": 135})
        == "理智估计已满（135/135）"
    )
    assert (
        summarize("device.unavailable", {"retry_attempt": 3})
        == "设备重连 3 次都失败，暂时不可用"
    )
    long_summary = summarize("pipeline.failed", {"title": "日" * 500})
    assert len(long_summary) == MAX_SUMMARY_CHARS
    assert long_summary.startswith("流水线「日日")
    assert summarize("no.such.kind", {}) == "no.such.kind"


def test_attention_kinds_are_core_failed_device_unavailable_and_two_goal_kinds():
    assert ATTENTION_KINDS == {
        "core.failed",
        "device.unavailable",
        "goal.completed",
        "goal.deadline_passed",
    }
    assert needs_attention("core.failed", {})
    assert needs_attention("device.unavailable", {"retry_attempt": 3})
    assert needs_attention("goal.completed", {"title": "银灰精二"})
    assert needs_attention("goal.deadline_passed", {"title": "银灰精二"})
    assert not needs_attention("goal.review_due", {"title": "银灰精二"})
    assert not needs_attention("core.crashed", {})
    assert not needs_attention("pipeline.failed", {})
    assert not needs_attention("no.such.kind", {})
