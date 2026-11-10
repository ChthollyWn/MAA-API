"""看板用的统计口径（M99 规格 §4.5.2）。

窗口与比率都按 spec 的唯一口径：

- 时间一律 naive UTC；本窗口 ``[now - days, now)``，上一窗口由调用方再减 ``days``；
- 比率类指标一律给 Wilson 95% 区间（``z = 1.96``），``n = 0`` 时返回 ``None``，
  区间夹到 ``[0, 1]``；
- 中位数用于对话效率及其 secondary，空样本返回 ``None``。

这里只放纯函数，不碰数据库，便于用已知数值钉死公式。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

__all__ = ["median", "rate", "wilson_interval", "window_bounds"]


def wilson_interval(
    successes: int, total: int, *, z: float = 1.96
) -> tuple[float, float] | None:
    """Wilson score 区间；``total <= 0`` 返回 ``None``，结果夹到 ``[0, 1]``。"""
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
        / denominator
    )
    low = max(0.0, centre - margin)
    high = min(1.0, centre + margin)
    return low, high


def rate(successes: int, total: int) -> float | None:
    """``successes / total``；``total <= 0`` 返回 ``None``，不写 0 冒充。"""
    if total <= 0:
        return None
    return successes / total


def median(values: Sequence[float]) -> float | None:
    """中位数；空样本返回 ``None``。不假设入参已排序。"""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def window_bounds(now: datetime, days: int) -> tuple[datetime, datetime]:
    """返回本窗口的 ``(起点, 终点)``；调用方用 ``[起点, 终点)`` 查询。

    上一窗口起点 = 本窗口起点再减 ``days``（spec §4.5.2）。
    """
    return now - timedelta(days=days), now
