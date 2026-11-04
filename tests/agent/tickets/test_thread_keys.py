"""话题键的解析、格式表与来源前缀（M69 规格 §4.2、§6；03 §15）。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from maa_api.agent.tickets.threads import (
    MAX_THREAD_KEY_CHARS,
    SOURCE_PREFIXES,
    THREAD_KEY_PATTERNS,
    RouteResult,
    ThreadKey,
    parse_thread_key,
)
from maa_api.domain.enums import EventSource
from maa_api.domain.errors import AppError, ErrorCode

_UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
_UUID_UPPER = _UUID.upper()

_VALID = [
    ("chat:orchestrator", "chat", "orchestrator"),
    (f"ticket:{_UUID}", "ticket", _UUID),
    (f"pipeline:{_UUID}", "pipeline", _UUID),
    ("core:default", "core", "default"),
    ("device:default", "device", "default"),
    ("schedule:rule_1", "schedule", "rule_1"),
    ("system:update_available", "system", "update_available"),
    ("im:feishu:oc_xxx", "im", "feishu:oc_xxx"),
    ("im:telegram:123456", "im", "telegram:123456"),
    (f"goal:{_UUID}", "goal", _UUID),
    ("watchdog:stuck_run", "watchdog", "stuck_run"),
]


def test_valid_thread_keys_parse() -> None:
    assert set(THREAD_KEY_PATTERNS) == {
        "chat",
        "ticket",
        "pipeline",
        "core",
        "device",
        "schedule",
        "system",
        "im",
        "goal",
        "watchdog",
    }
    for raw, prefix, value in _VALID:
        key = parse_thread_key(raw)
        assert isinstance(key, ThreadKey)
        assert key.raw == raw
        assert key.prefix == prefix
        assert key.value == value
    with pytest.raises(FrozenInstanceError):
        key.raw = "chat:other"  # type: ignore[misc]


def test_invalid_thread_keys_raise() -> None:
    invalid = [
        "",
        "no-colon",
        "chat:other",
        "chat:orchestrator ",
        "chat:orchestrator\n",
        "system:update available",
        "ticket:",
        f"ticket:{_UUID_UPPER}",
        "ticket:not-a-uuid",
        "pipeline:ABC",
        "core:",
        "core:Default",
        "system:Update",
        "foo:1",
        "im:slack:oc_xxx",
        f"system:{'a' * MAX_THREAD_KEY_CHARS}",
    ]
    for raw in invalid:
        with pytest.raises(AppError) as exc_info:
            parse_thread_key(raw)
        error = exc_info.value
        assert error.code is ErrorCode.INVALID_PARAMETER, raw
        assert error.message == "话题键格式不对"
        assert error.details == {"thread_key": raw}


def test_source_prefix_mapping_covers_every_event_source() -> None:
    assert set(SOURCE_PREFIXES) == set(EventSource)
    assert SOURCE_PREFIXES == {
        EventSource.WEB: frozenset({"chat", "ticket"}),
        EventSource.IM_TELEGRAM: frozenset({"im"}),
        EventSource.IM_FEISHU: frozenset({"im"}),
        EventSource.IM_DINGTALK: frozenset({"im"}),
        EventSource.IM_QQ: frozenset({"im"}),
        EventSource.PIPELINE: frozenset({"pipeline"}),
        EventSource.CORE: frozenset({"core"}),
        EventSource.DEVICE: frozenset({"device"}),
        EventSource.SCHEDULE: frozenset({"schedule"}),
        EventSource.SYSTEM: frozenset({"system"}),
        EventSource.WATCHDOG: frozenset({"watchdog"}),
        EventSource.GOAL: frozenset({"goal"}),
    }
    for prefixes in SOURCE_PREFIXES.values():
        assert prefixes <= set(THREAD_KEY_PATTERNS)


def test_route_result_shape() -> None:
    result = RouteResult(
        routed_to="ticket",
        inbox_ids=(1, 2),
        ticket_ids=("ticket-1", "ticket-2"),
    )
    assert result.routed_to == "ticket"
    assert result.inbox_ids == (1, 2)
    assert result.ticket_ids == ("ticket-1", "ticket-2")
    with pytest.raises(FrozenInstanceError):
        result.routed_to = "none"  # type: ignore[misc]
