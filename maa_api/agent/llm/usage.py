"""模型用量归一（规格 §4.2.2、docs/v3/06 §2.8）。

各家 OpenAI 兼容服务的 usage 字段并不一致：DeepSeek 在顶层给
``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``，OpenAI 与部分兼容服务
把缓存命中放在 ``prompt_tokens_details.cached_tokens``，思考 token 放在
``completion_tokens_details.reasoning_tokens``。本模块把它们归一成 :class:`Usage`
的四个计数，供成本账本、预算与上下文校准共用；计费时思考 token 已包含在
``completion_tokens`` 里，不再重复加。

``Usage`` 只认非负 ``int``（``bool`` 是 ``int`` 的子类，但 ``True`` 不能当 1 个
token），其余一律按 0；``raw`` 为 ``None`` 时返回全 0 的空用量。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["Usage", "normalize_usage"]


def _token_count(value: Any) -> int | None:
    """合法计数原样返回；不是非负 ``int``（含 ``bool``）时返回 ``None``。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _detail_count(raw: Mapping[str, Any], key: str, field: str) -> int | None:
    """取 ``raw[key][field]`` 的合法计数；``raw[key]`` 不是映射或值非法时为 ``None``。"""
    details = raw.get(key)
    if not isinstance(details, Mapping):
        return None
    return _token_count(details.get(field))


@dataclass(frozen=True, slots=True)
class Usage:
    """一次模型调用的四个 token 计数（默认全 0），支持逐项相加。"""

    #: 输入 token 总数（含缓存命中部分）。
    prompt_tokens: int = 0
    #: 输出 token 总数（含思考 token）。
    completion_tokens: int = 0
    #: 输入中命中前缀缓存的部分，恒 ≤ ``prompt_tokens``。
    cached_tokens: int = 0
    #: 输出中的思考 token，恒 ≤ ``completion_tokens``；计费不重复加。
    reasoning_tokens: int = 0

    def __add__(self, other: object) -> Usage:
        if not isinstance(other, Usage):
            return NotImplemented
        # 兼容 M65 的旧构造口径：当时两个字段是可选的 ``None``。
        return Usage(
            prompt_tokens=(self.prompt_tokens or 0) + (other.prompt_tokens or 0),
            completion_tokens=(self.completion_tokens or 0)
            + (other.completion_tokens or 0),
            cached_tokens=(self.cached_tokens or 0) + (other.cached_tokens or 0),
            reasoning_tokens=(self.reasoning_tokens or 0)
            + (other.reasoning_tokens or 0),
        )


def normalize_usage(raw: Mapping[str, Any] | None) -> Usage:
    """把一次上游 usage 原始映射归一成 :class:`Usage`。

    - prompt 取 ``raw["prompt_tokens"]``；它缺失或非法、且有
      ``prompt_cache_hit_tokens`` 与 ``prompt_cache_miss_tokens`` 时取两者之和
      （DeepSeek 口径）。
    - cached 依次取 ``prompt_cache_hit_tokens``（DeepSeek）、
      ``prompt_tokens_details.cached_tokens``（OpenAI 等）、顶层 ``cached_tokens``，
      第一个合法值为准，否则 0；再截到 ≤ prompt。
    - completion 取 ``completion_tokens``；reasoning 取
      ``completion_tokens_details.reasoning_tokens``，再截到 ≤ completion。
      思考 token 已包含在 completion 里，计费时不重复加。
    - 任何值不是非负 ``int``（含 ``bool``）都按 0；``raw`` 为 ``None`` 返回
      全 0 的 ``Usage()``。
    """
    if not isinstance(raw, Mapping):
        return Usage()

    prompt = _token_count(raw.get("prompt_tokens"))
    if prompt is None:
        cache_hit = _token_count(raw.get("prompt_cache_hit_tokens"))
        cache_miss = _token_count(raw.get("prompt_cache_miss_tokens"))
        if cache_hit is not None and cache_miss is not None:
            prompt = cache_hit + cache_miss
    prompt = prompt or 0

    cached = _token_count(raw.get("prompt_cache_hit_tokens"))
    if cached is None:
        cached = _detail_count(raw, "prompt_tokens_details", "cached_tokens")
    if cached is None:
        cached = _token_count(raw.get("cached_tokens"))
    cached = min(cached or 0, prompt)

    completion = _token_count(raw.get("completion_tokens")) or 0
    reasoning = _detail_count(raw, "completion_tokens_details", "reasoning_tokens")
    reasoning = min(reasoning or 0, completion)

    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
        reasoning_tokens=reasoning,
    )
