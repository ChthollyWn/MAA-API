"""M67 T7：前缀缓存布局的渲染与尾部合并（规格 §4.2.6、§6）。"""

from __future__ import annotations

import copy

from maa_api.agent.context.layout import (
    SYSTEM_SEGMENT_ORDER,
    TAIL_HEADER,
    TAIL_SEGMENT_ORDER,
    ContextLayout,
    ContextParts,
    Segment,
    merge_tail,
    put_segment,
    render_tail_text,
)
from maa_api.agent.context.tokens import TokenEstimator

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "查一下",
            "parameters": {"type": "object", "properties": {}},
        },
    }
]


def test_render_orders_segments_and_tail() -> None:
    parts = ContextParts(
        # 故意乱序写入：渲染必须按 SYSTEM_SEGMENT_ORDER 重排。
        system=[
            Segment("knowledge", "知识库稳定段"),
            Segment("spec", "代理定义正文"),
        ],
        history=[{"role": "user", "content": "帮我查一下"}],
        tail=[
            Segment("knowledge", "知识库动态段"),
            Segment("status", "状态摘要：{}"),
        ],
        tools=list(_TOOLS),
        position="tail_system",
    )
    history_before = copy.deepcopy(parts.history)

    messages = ContextLayout(TokenEstimator()).render(parts)

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "system",
    ]
    system = messages[0]["content"]
    # system 段按 SYSTEM_SEGMENT_ORDER（spec 在 knowledge 之前）。
    assert SYSTEM_SEGMENT_ORDER.index("spec") < SYSTEM_SEGMENT_ORDER.index("knowledge")
    assert system.index("代理定义正文") < system.index("知识库稳定段")
    # 尾部以固定开头开始，段顺序同样是固定顺序（status 在 knowledge 之前）。
    assert TAIL_SEGMENT_ORDER.index("status") < TAIL_SEGMENT_ORDER.index("knowledge")
    tail = messages[-1]["content"]
    assert tail.startswith(TAIL_HEADER)
    assert tail.index("状态摘要：{}") < tail.index("知识库动态段")
    # 历史消息与 parts 本身都没被改写。
    assert messages[1] is parts.history[0]
    assert parts.history == history_before


def test_render_skips_empty_tail_and_keeps_parts_history_objects() -> None:
    parts = ContextParts(
        system=[Segment("spec", "定义")],
        history=[{"role": "user", "content": "问"}],
    )
    messages = ContextLayout(TokenEstimator()).render(parts)

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[1] is parts.history[0]
    assert render_tail_text(parts.tail) == ""


def test_put_segment_orders_and_replaces() -> None:
    segments = [Segment("spec", "一"), Segment("knowledge", "三")]
    put_segment(segments, Segment("common", "二"), SYSTEM_SEGMENT_ORDER)
    assert [segment.name for segment in segments] == ["spec", "common", "knowledge"]

    put_segment(segments, Segment("spec", "改"), SYSTEM_SEGMENT_ORDER)
    assert [(segment.name, segment.text) for segment in segments] == [
        ("spec", "改"),
        ("common", "二"),
        ("knowledge", "三"),
    ]

    try:
        put_segment(segments, Segment("unknown", "x"), SYSTEM_SEGMENT_ORDER)
    except ValueError:
        pass
    else:  # pragma: no cover - 名字不在 order 里必须 fail loud
        raise AssertionError("未知段名应当抛 ValueError")


def test_tail_user_merges_into_the_last_user_or_tool_message() -> None:
    # 1. 最后一条是字符串 user：后接一个空行再接尾部；原 dict 不改。
    history = [{"role": "user", "content": "原话"}]
    merged = merge_tail(history, "尾部文本", "tail_user")
    assert merged[-1] == {"role": "user", "content": "原话\n\n尾部文本"}
    assert history == [{"role": "user", "content": "原话"}]
    assert merged is not history

    # 2. 最后一条是字符串 tool：同样并进 content，tool_call_id 保留。
    history = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": '{"result": 1}'},
    ]
    merged = merge_tail(history, "尾部文本", "tail_user")
    assert merged[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"result": 1}\n\n尾部文本',
    }
    assert history[-1] == {"role": "tool", "tool_call_id": "call-1", "content": '{"result": 1}'}

    # 3. 最后一条是数组 tool：末尾追加一个 text 部分，原数组不动。
    parts = [{"type": "text", "text": "结果"}]
    history = [{"role": "tool", "tool_call_id": "call-2", "content": parts}]
    merged = merge_tail(history, "尾部文本", "tail_user")
    assert merged[-1]["content"] == [
        {"type": "text", "text": "结果"},
        {"type": "text", "text": "尾部文本"},
    ]
    assert parts == [{"type": "text", "text": "结果"}]

    # 4. 最后一条是 assistant：追加一条 user 消息，而不是并进 assistant。
    history = [{"role": "assistant", "content": "说完了"}]
    merged = merge_tail(history, "尾部文本", "tail_user")
    assert merged[-1] == {"role": "user", "content": "尾部文本"}
    assert len(merged) == len(history) + 1
    assert history == [{"role": "assistant", "content": "说完了"}]

    # tail_system 则是独立的一条 system 消息。
    system_tail = merge_tail(history, "尾部文本", "tail_system")
    assert system_tail[-1] == {"role": "system", "content": "尾部文本"}
