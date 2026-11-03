"""上下文布局与 token 估算（M67 规格 §4.2.6、v3 06 §2–§3）。

- :mod:`maa_api.agent.context.tokens`：按字符分类的估算与按档案的 usage 校准；
- :mod:`maa_api.agent.context.layout`：稳定段、历史与动态尾部的固定渲染，以及
  前缀断点的比较与记录。

legacy 定义（``legacy_chat`` / ``legacy_incident``）继续走 v2 的 ``build_model_context``
与字符预算，不经过这里；新代理的输入由 ``ContextLayoutMiddleware`` 组装。
"""

from __future__ import annotations

from maa_api.agent.context.layout import (
    SYSTEM_SEGMENT_ORDER,
    TAIL_HEADER,
    TAIL_SEGMENT_ORDER,
    ContextLayout,
    ContextParts,
    Segment,
    merge_tail,
    parts_of,
    put_segment,
    render_tail_text,
)
from maa_api.agent.context.tokens import (
    CALIBRATION_ALPHA,
    CALIBRATION_BOUNDS,
    CALIBRATION_MIN_RAW,
    CALIBRATION_SAMPLE_BOUNDS,
    CJK_PATTERN,
    CJK_TOKENS_PER_CHAR,
    IMAGE_TOKENS,
    MESSAGE_OVERHEAD_TOKENS,
    OTHER_TOKENS_PER_CHAR,
    TOOL_OVERHEAD_TOKENS,
    TokenEstimator,
    estimate_text,
)

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
    "SYSTEM_SEGMENT_ORDER",
    "TAIL_HEADER",
    "TAIL_SEGMENT_ORDER",
    "TOOL_OVERHEAD_TOKENS",
    "ContextLayout",
    "ContextParts",
    "Segment",
    "TokenEstimator",
    "estimate_text",
    "merge_tail",
    "parts_of",
    "put_segment",
    "render_tail_text",
]
