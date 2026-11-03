"""token 估算与按档案校准（M67 规格 §4.2.6、v3 06 §3）。

不引入 tokenizer（V3-D37）：按字符分类与固定结构开销估算，再用上游返回的
``usage.prompt_tokens`` 按档案维护一个 EMA 比例。比例只在进程内存里，按
``(档案名, model)`` 分开；重启后从 1.0 重新收敛。

估算口径（v3 06 §3.1）：

- 每条消息的 ``content``（字符串或多模态数组，数组里的图片按固定值）、
  ``reasoning_content``、``tool_calls`` 的 JSON 文本；
- 每条消息加 :data:`MESSAGE_OVERHEAD_TOKENS`；每个工具声明加
  :data:`TOOL_OVERHEAD_TOKENS`，另按它的 JSON 文本计字符；
- 结果向上取整。

图片不参与校准（v3 06 §3.2）：各服务对图片的计价差别太大，样本里含图片会污染
比例。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "CALIBRATION_ALPHA",
    "CALIBRATION_BOUNDS",
    "CALIBRATION_MIN_RAW",
    "CALIBRATION_SAMPLE_BOUNDS",
    "CJK_PATTERN",
    "CJK_TOKENS_PER_CHAR",
    "IMAGE_TOKENS",
    "MESSAGE_OVERHEAD_TOKENS",
    "OTHER_TOKENS_PER_CHAR",
    "TOOL_OVERHEAD_TOKENS",
    "TokenEstimator",
    "estimate_text",
]

#: v3 06 §3.1 的初始系数。CJK 汉字、假名与全角标点按 0.6 token/字，其余 0.3。
CJK_TOKENS_PER_CHAR = 0.6
OTHER_TOKENS_PER_CHAR = 0.3
#: 每条消息的结构开销（角色与分隔符）。
MESSAGE_OVERHEAD_TOKENS = 4
#: 每个工具声明的结构开销；另按它的 JSON 文本计字符。
TOOL_OVERHEAD_TOKENS = 8
#: 一张图片的固定折算（v2 ``IMAGE_CONTEXT_CHARS = 4 000`` 字按 2.5 字/token 折算）。
IMAGE_TOKENS = 1600

#: 校准的 EMA 系数（v3 06 §3.2）。
CALIBRATION_ALPHA = 0.2
#: 校准比例的上下限。
CALIBRATION_BOUNDS = (0.5, 2.0)
#: 参与校准的 ``sample = prompt_tokens / raw_estimate`` 区间；区间外跳过。
CALIBRATION_SAMPLE_BOUNDS = (0.25, 4.0)
#: raw_estimate 低于此值时不校准：太小的请求受固定开销与缓存计量影响太大。
CALIBRATION_MIN_RAW = 500

#: CJK 判定（规格逐字给定）。
CJK_PATTERN = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]"
)


def estimate_text(text: str) -> int:
    """估算一段纯文本的 token：``ceil(cjk × 0.6 + other × 0.3)``。"""
    if not text:
        return 0
    cjk = len(CJK_PATTERN.findall(text))
    other = len(text) - cjk
    return math.ceil(cjk * CJK_TOKENS_PER_CHAR + other * OTHER_TOKENS_PER_CHAR)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _content_tokens(content: Any) -> int:
    """一条消息的 content：字符串、多模态数组或其它 JSON 值。"""
    if isinstance(content, str):
        return estimate_text(content)
    if isinstance(content, (list, tuple)):
        total = 0
        for part in content:
            if not isinstance(part, Mapping):
                total += estimate_text(_json_text(part))
                continue
            if str(part.get("type")) == "image_url":
                total += IMAGE_TOKENS
                continue
            text = part.get("text")
            total += (
                estimate_text(text)
                if isinstance(text, str)
                else estimate_text(_json_text(part))
            )
        return total
    if content is None:
        return 0
    return estimate_text(_json_text(content))


class TokenEstimator:
    """按字符分类估算请求 token，并按档案用上游 usage 校准比例。"""

    def __init__(self) -> None:
        self._ratios: dict[tuple[str, str], float] = {}

    def raw_estimate(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] = (),
    ) -> int:
        """未乘校准比例的估算（图片按固定值、结构开销按常量）。"""
        total = 0
        for message in messages:
            total += MESSAGE_OVERHEAD_TOKENS
            total += _content_tokens(message.get("content"))
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str):
                total += estimate_text(reasoning)
            calls = message.get("tool_calls")
            if calls:
                total += estimate_text(_json_text(calls))
        for tool in tools:
            total += TOOL_OVERHEAD_TOKENS
            total += estimate_text(_json_text(tool))
        return total

    def ratio(self, key: tuple[str, str]) -> float:
        """某个 ``(档案名, model)`` 当前的校准比例；没见过时为 1.0。"""
        return self._ratios.get(tuple(key), 1.0)

    def estimate(
        self,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]] = (),
        *,
        key: tuple[str, str] | None = None,
    ) -> int:
        """``ceil(raw × ratio)``；没有 key 时不校准。"""
        raw = self.raw_estimate(messages, tools)
        ratio = self.ratio(key) if key is not None else 1.0
        return math.ceil(raw * ratio)

    def observe(
        self,
        key: tuple[str, str],
        *,
        raw_estimate: int,
        prompt_tokens: int,
        has_images: bool,
    ) -> float:
        """用一次真实调用校准比例；不满足条件的样本原样返回当前比例。

        跳过：``has_images``、``raw_estimate < CALIBRATION_MIN_RAW``、
        ``sample`` 不在 :data:`CALIBRATION_SAMPLE_BOUNDS` 里。
        """
        current = self.ratio(key)
        if has_images or raw_estimate < CALIBRATION_MIN_RAW:
            return current
        if raw_estimate <= 0 or prompt_tokens <= 0:
            return current
        sample = prompt_tokens / raw_estimate
        low_sample, high_sample = CALIBRATION_SAMPLE_BOUNDS
        if sample < low_sample or sample > high_sample:
            return current
        low, high = CALIBRATION_BOUNDS
        updated = current * (1 - CALIBRATION_ALPHA) + sample * CALIBRATION_ALPHA
        updated = min(max(updated, low), high)
        self._ratios[tuple(key)] = updated
        return updated
