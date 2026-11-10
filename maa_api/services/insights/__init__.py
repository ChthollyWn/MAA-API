"""能力效果看板的指标目录、统计与聚合服务（M99 规格 §4.2 第 3 条）。

重新导出路由层与测试要用的四个名字；具体实现分在 ``catalog`` / ``stats`` /
``service`` 三个模块里。
"""

from __future__ import annotations

from maa_api.services.insights.catalog import METRICS, MetricDefinition
from maa_api.services.insights.service import (
    BreakdownData,
    BreakdownItem,
    FeedbackDigestData,
    FeedbackItem,
    FeedbackReasonCount,
    InsightsService,
    MetricCard,
    MetricReading,
    OverviewData,
    SubMetric,
    TrendData,
    TrendPoint,
)
from maa_api.services.insights.stats import wilson_interval

__all__ = [
    "METRICS",
    "BreakdownData",
    "BreakdownItem",
    "FeedbackDigestData",
    "FeedbackItem",
    "FeedbackReasonCount",
    "InsightsService",
    "MetricCard",
    "MetricDefinition",
    "MetricReading",
    "OverviewData",
    "SubMetric",
    "TrendData",
    "TrendPoint",
    "wilson_interval",
]
