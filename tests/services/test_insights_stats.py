"""能力效果看板的统计口径（M99 规格 §4.5.2）。

- 比率类指标一律用 Wilson 95% 区间（``z = 1.96``）；``n = 0`` 返回 ``None``，
  区间夹到 ``[0, 1]``；
- 中位数对奇数与偶数样本都要成立（对话效率与在线 secondary 用）；
- 窗口左闭右开：本窗口 ``[now - days, now)``，上一窗口由调用方再减 ``days``。
"""

from datetime import datetime, timedelta

from maa_api.services.insights.stats import (
    median,
    rate,
    wilson_interval,
    window_bounds,
)

NOW = datetime(2026, 11, 10, 12, 0, 0)


def test_wilson_interval_known_values() -> None:
    """手算的 7/10：Wilson 95% 区间约为 0.3968–0.8922（spec §4.5.2）。"""
    low, high = wilson_interval(7, 10)
    assert abs(low - 0.3968) < 0.0001
    assert abs(high - 0.8922) < 0.0001


def test_wilson_zero_total_is_none() -> None:
    """没有可判定样本时不给区间，不写 0 冒充。"""
    assert wilson_interval(0, 0) is None


def test_wilson_clamps_to_unit_interval() -> None:
    """0/10 与 10/10 的浮点边界必须夹到 0 与 1。"""
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert 0.0 <= low <= high <= 1.0

    low, high = wilson_interval(10, 10)
    assert high == 1.0
    assert 0.0 <= low <= high <= 1.0


def test_rate_returns_none_for_empty_sample() -> None:
    assert rate(0, 0) is None
    assert rate(1, 4) == 0.25


def test_median_even_and_odd() -> None:
    """中位数：奇数取中间，偶数取中间两个的均值，空样本 None。"""
    assert median([3, 1, 2]) == 2.0
    assert median([4, 1, 3, 2]) == 2.5
    assert median([]) is None
    assert median([5]) == 5.0


def test_window_bounds_are_half_open() -> None:
    """本窗口左闭右开 ``[now - days, now)``；上一窗口起点再减 days。"""
    start, end = window_bounds(NOW, 30)
    assert start == NOW - timedelta(days=30)
    assert end == NOW
    assert start.tzinfo is None and end.tzinfo is None

    # 起点属于本窗口，终点属于下一窗口：服务层一律用 ``>= :start`` 与
    # ``< :end`` 查询，这里钉死边界值。
    previous_start = start - timedelta(days=30)
    assert previous_start == NOW - timedelta(days=60)
    assert previous_start < start < end
