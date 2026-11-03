"""M67 T3：用量归一（规格 §4.2.2、docs/v3/06 §2.8）。"""

from __future__ import annotations

import asyncio
from typing import Any

from maa_api.agent.harness.chunks import collect_completion
from maa_api.agent.harness.context import Usage as HarnessUsage
from maa_api.agent.llm.usage import Usage, normalize_usage


def test_usage_is_shared_with_the_harness_context() -> None:
    """``harness.context.Usage`` 是从 ``llm.usage`` 再导出的同一个对象。"""
    assert HarnessUsage is Usage


def test_deepseek_cache_fields_map_to_cached_tokens() -> None:
    """DeepSeek 没有 ``prompt_tokens`` 时，prompt 取命中 + 未命中之和。"""
    usage = normalize_usage(
        {"prompt_cache_hit_tokens": 30, "prompt_cache_miss_tokens": 70}
    )

    assert usage == Usage(prompt_tokens=100, cached_tokens=30)


def test_openai_details_reasoning_and_invalid_values() -> None:
    """OpenAI 的 details 映射；cached 截到 ≤ prompt，reasoning 截到 ≤ completion。"""
    usage = normalize_usage(
        {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 60},
            "completion_tokens_details": {"reasoning_tokens": 25},
        }
    )
    assert usage == Usage(
        prompt_tokens=100,
        completion_tokens=40,
        cached_tokens=60,
        reasoning_tokens=25,
    )

    capped = normalize_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 99},
            "completion_tokens_details": {"reasoning_tokens": 99},
        }
    )
    assert capped == Usage(
        prompt_tokens=10, completion_tokens=5, cached_tokens=10, reasoning_tokens=5
    )

    # 负数、bool、字符串都不是非负 int：各自按 0，上限也一起回落。
    invalid = normalize_usage(
        {
            "prompt_tokens": True,
            "completion_tokens": "12",
            "cached_tokens": -3,
            "prompt_tokens_details": {"cached_tokens": False},
            "completion_tokens_details": {"reasoning_tokens": -1},
        }
    )
    assert invalid == Usage()


def test_cached_falls_back_through_deepseek_openai_and_top_level() -> None:
    # 三个来源都有时，DeepSeek 的字段优先。
    assert normalize_usage(
        {
            "prompt_tokens": 100,
            "prompt_cache_hit_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 60},
            "cached_tokens": 80,
        }
    ).cached_tokens == 30
    # DeepSeek 字段缺失才看 OpenAI details，再退到顶层 cached_tokens。
    assert normalize_usage(
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 60}}
    ).cached_tokens == 60
    assert normalize_usage(
        {"prompt_tokens": 100, "cached_tokens": 80}
    ).cached_tokens == 80


def test_none_returns_empty_usage_and_add_sums_each_field() -> None:
    assert normalize_usage(None) == Usage()
    assert normalize_usage({}) == Usage()
    assert Usage(1, 2, 3, 4) + Usage(10, 20, 30, 40) == Usage(11, 22, 33, 44)


class _StreamingClient:
    """只实现 ``stream_chat_completion`` 的最小假客户端。"""

    def __init__(self, *chunks: Any) -> None:
        self.chunks = list(chunks)

    async def stream_chat_completion(self, model, messages, tools, **options):
        for chunk in self.chunks:
            yield chunk


def test_collect_completion_uses_the_normalized_usage() -> None:
    async def emit(_name, _data):
        return None

    client = _StreamingClient(
        {
            "choices": [],
            "usage": {
                "prompt_cache_hit_tokens": 30,
                "prompt_cache_miss_tokens": 70,
                "completion_tokens": 8,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        },
        {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]},
    )

    response = asyncio.run(collect_completion(client, "m", [], [], emit=emit))

    assert response.usage == Usage(
        prompt_tokens=100,
        completion_tokens=8,
        cached_tokens=30,
        reasoning_tokens=3,
    )
