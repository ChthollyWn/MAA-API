"""M67 T7：token 估算与校准（规格 §4.2.6、§6）。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from maa_api.agent.context.tokens import (
    CALIBRATION_BOUNDS,
    DEFAULT_OUTPUT_RESERVE,
    IMAGE_TOKENS,
    MESSAGE_OVERHEAD_TOKENS,
    TOOL_OVERHEAD_TOKENS,
    TokenEstimator,
    budget_for,
    estimate_text,
)


def test_coefficients_and_calibration() -> None:
    # CJK 0.6 token/字：两个汉字 → ceil(1.2) = 2；四个其它字符 → ceil(1.2) = 2。
    assert estimate_text("你好") == 2
    assert estimate_text("abcd") == 2
    assert estimate_text("") == 0

    estimator = TokenEstimator()
    # 一条消息的正文之外加固定的结构开销。
    assert estimator.raw_estimate([{"role": "user", "content": "你好"}]) == (
        2 + MESSAGE_OVERHEAD_TOKENS
    )
    # 多模态数组里的图片按固定值计，文本部分照常估。
    image_message = {
        "role": "tool",
        "content": [
            {"type": "text", "text": "看一下"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
        ],
    }
    assert estimator.raw_estimate([image_message]) == (
        estimate_text("看一下") + IMAGE_TOKENS + MESSAGE_OVERHEAD_TOKENS
    )
    # 每个工具声明加结构开销，另按它的 JSON 文本计字符。
    tool = {"type": "function", "function": {"name": "lookup", "parameters": {}}}
    assert estimator.raw_estimate([], [tool]) == (
        TOOL_OVERHEAD_TOKENS
        + estimate_text(json.dumps(tool, ensure_ascii=False))
    )

    # 样本 1.5 → ratio = 1.0 × 0.8 + 1.5 × 0.2 = 1.1。
    key = ("default", "model-a")
    assert estimator.ratio(key) == 1.0
    assert estimator.observe(
        key, raw_estimate=1000, prompt_tokens=1500, has_images=False
    ) == pytest.approx(1.1)
    assert estimator.ratio(key) == pytest.approx(1.1)

    # 含图片、raw 太小、sample 越界的样本都不改比例。
    assert estimator.observe(
        key, raw_estimate=1000, prompt_tokens=4000, has_images=True
    ) == pytest.approx(1.1)
    assert estimator.observe(
        key, raw_estimate=100, prompt_tokens=150, has_images=False
    ) == pytest.approx(1.1)
    assert estimator.observe(
        key, raw_estimate=1000, prompt_tokens=10, has_images=False
    ) == pytest.approx(1.1)
    assert estimator.observe(
        key, raw_estimate=1000, prompt_tokens=5000, has_images=False
    ) == pytest.approx(1.1)

    # 估算 = ceil(raw × ratio)：raw = 2 + 4 = 6；6 × 1.1 = 6.6 → 向上取整 7。
    assert estimator.estimate(
        [{"role": "user", "content": "你好"}], key=key
    ) == 7


def test_ratio_stays_inside_the_documented_bounds() -> None:
    estimator = TokenEstimator()
    high_key = ("high", "model")
    low_key = ("low", "model")
    for _ in range(10):
        estimator.observe(
            high_key, raw_estimate=1000, prompt_tokens=4000, has_images=False
        )
        estimator.observe(
            low_key, raw_estimate=1000, prompt_tokens=250, has_images=False
        )
    low, high = CALIBRATION_BOUNDS
    assert estimator.ratio(high_key) == high
    assert estimator.ratio(low_key) == low


class _Profile:
    """``budget_for`` 只需要窗口与输出预留两个入口。"""

    def __init__(self, *, context_window: int | None, max_output: int | None) -> None:
        self.context_window = context_window
        self.max_output_tokens = max_output

    def window(self, fallback: int) -> int:
        return self.context_window or fallback


def _context_settings(**overrides: object) -> SimpleNamespace:
    values = {
        "fallback_context_tokens": 128000,
        "compaction_ratio": 0.75,
    }
    values.update(overrides)
    return SimpleNamespace(context=SimpleNamespace(**values))


def test_budget_for_uses_profile_window_and_output_reserve() -> None:
    # 档案没写窗口：用 context.fallback_context_tokens；输出预留取默认 8192。
    budget = budget_for(
        _Profile(context_window=None, max_output=None), _context_settings()
    )
    assert DEFAULT_OUTPUT_RESERVE == 8192
    assert (budget.window, budget.output_reserve) == (128000, 8192)
    assert budget.overflow_tokens == 128000 - 8192
    assert budget.trigger_tokens == 96000

    # 输出预留不超过窗口的四分之一：窗口 10 000 时只留 2 500。
    small = budget_for(
        _Profile(context_window=10000, max_output=8192),
        _context_settings(compaction_ratio=0.95),
    )
    assert (small.window, small.output_reserve) == (10000, 2500)
    assert small.overflow_tokens == 7500
    # 阈值被溢出线压住，压缩不会晚于溢出。
    assert small.trigger_tokens == 7500

    # 档案写了输出预留时用它；阈值 = min(比例 × 窗口, 溢出线)。
    large = budget_for(
        _Profile(context_window=100000, max_output=20000),
        _context_settings(compaction_ratio=0.5),
    )
    assert (large.window, large.output_reserve) == (100000, 20000)
    assert large.overflow_tokens == 80000
    assert large.trigger_tokens == 50000
