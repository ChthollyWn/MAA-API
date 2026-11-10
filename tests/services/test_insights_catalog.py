"""指标目录与 spec §4.5.1 的逐项一致性（M99 规格 §4.5.1）。

目录是看板「评估方法」一节的唯一口径：``definition``/``formula``/``source``/
``evaluation``/``limitations`` 都原样展示，因此这里用快照钉死，避免实现时
顺手润色成别的意思。
"""

from maa_api.domain.enums import EvidenceGrade, MetricId, MetricScope
from maa_api.services.insights.catalog import METRICS

#: spec §4.5.1 的固定顺序。
EXPECTED_ORDER = (
    MetricId.ACCURACY,
    MetricId.HALLUCINATION_RATE,
    MetricId.SAFETY_RATE,
    MetricId.TASK_COMPLETION,
    MetricId.CONVERSATION_EFFICIENCY,
    MetricId.COST,
    MetricId.USER_SATISFACTION,
)

OFFLINE_SOURCE = "`eval_case_result`、`eval_run`"

#: (label, scope, unit, evidence, min_sample, supports_breakdown, definition,
#:  formula, source, evaluation, limitations)
EXPECTED = {
    MetricId.ACCURACY: (
        "准确率",
        MetricScope.OFFLINE,
        "ratio",
        EvidenceGrade.MEASURED,
        10,
        False,
        "标了 `accuracy` 的 capability 用例在 `record`/`live` 运行中的通过率",
        "`passed / (passed + failed)`；`skipped`、`error` 不计",
        OFFLINE_SOURCE,
        "场景断言：`tool_sequence`、`outcome_state`、`text_contains` 确定性判分；"
        "14 个场景，见 §4.5.3",
        (
            "replay 只作回归基线，不进入能力口径",
            "分母只含 passed + failed，skipped/error 不计，"
            "与 10 §6.8 的 pass_rate 不同",
            "只覆盖标注了 accuracy 的 capability 场景",
        ),
    ),
    MetricId.HALLUCINATION_RATE: (
        "幻觉率",
        MetricScope.OFFLINE,
        "ratio",
        EvidenceGrade.MEASURED,
        5,
        False,
        "标了 `hallucination_rate` 的 2 个场景中，出现无依据断言的用例比例",
        "`1 - passed / (passed + failed)`",
        OFFLINE_SOURCE,
        "场景给出限定资料或未知问题，断言要求「只依据资料」或明确表示无法确认，"
        "禁止编造具体数值/结论",
        (
            "只覆盖标注 hallucination_rate 的 2 个场景，是场景近似而非全量统计",
            "replay 只作回归基线，不进入能力口径",
        ),
    ),
    MetricId.SAFETY_RATE: (
        "安全率",
        MetricScope.OFFLINE,
        "ratio",
        EvidenceGrade.MEASURED,
        6,
        False,
        "标了 `safety_rate` 的 6 个场景的通过率",
        "4 个攻击场景要求不调用危险工具并明确拒绝，2 个对照场景要求正常完成",
        OFFLINE_SOURCE,
        "场景断言同准确率；对照场景用来发现误拒",
        (
            "只覆盖标注 safety_rate 的 6 个场景",
            "replay 只作回归基线，不进入能力口径",
        ),
    ),
    MetricId.TASK_COMPLETION: (
        "任务完成率",
        MetricScope.ONLINE,
        "ratio",
        EvidenceGrade.SELF_REPORTED,
        20,
        True,
        "窗口内进入终态的工单中 `done` 的比例",
        "`done / (done + failed + cancelled)`；终态时间取 `ended_at`，"
        "缺失时取 `updated_at`",
        "`ticket`",
        "工单终态口径与 10 §7.3 相同；`done` 目前是 Worker 自述，"
        "附机器验证子集（`ticket.result.verification.method` 为 `machine`/`verifier`）",
        (
            "done 目前是 Worker 自述，需结合机器验证子集看",
            "只统计进入终态的工单，open/waiting/blocked 不计",
            "窗口按终态时间过滤，不按 created_at",
        ),
    ),
    MetricId.CONVERSATION_EFFICIENCY: (
        "对话效率",
        MetricScope.ONLINE,
        "turns",
        EvidenceGrade.MEASURED,
        20,
        True,
        "窗口内 `done` 工单的用户轮次中位数",
        "每条工单取 `agent_inbox` 里 `session_id = ticket.worker_session_id`、"
        "`kind = user_message` 的条数，再取中位数；"
        "不数 `agent_message.role=user`（汇报与事件卡片也以 user 角色落库）",
        "`ticket`、`agent_inbox`、`agent_run`",
        "只对完成工单条件化；中位数抗长尾；另给每单运行次数（`agent_run` 行数）、"
        "每单模型调用数（`Σ agent_run.steps`）与每单 token",
        (
            "只对 done 工单条件化，失败与取消的轮次不计",
            "没有 worker_session_id 或没有用户消息的工单不进入中位数样本",
            "用户轮次取 agent_inbox.user_message，不数 agent_message.role=user",
        ),
    ),
    MetricId.COST: (
        "成本",
        MetricScope.ONLINE,
        "micros",
        EvidenceGrade.MEASURED,
        20,
        True,
        "窗口内 `done` 工单的平均 Agent 成本",
        "`Σ agent_run.cost_micros / done 工单数`；窗口内出现多种币种时不给合并值",
        "`agent_run`、`cost_ledger`",
        "成本账本与运行行同一口径（10 §4.1）；分币种汇总，"
        "另给每完成工单 token 与缓存命中率",
        (
            "窗口内出现多种币种时主值置空，分币种单列",
            "只统计 done 工单关联的 agent_run",
            "currency 为空的行按「未知」单列",
        ),
    ),
    MetricId.USER_SATISFACTION: (
        "用户满意度",
        MetricScope.ONLINE,
        "ratio",
        EvidenceGrade.SELF_REPORTED,
        20,
        True,
        "窗口内新增评价中 `rating ≥ 4` 的比例（正向率）",
        "`positive / total`",
        "`run_feedback`、`agent_run`",
        "运行详情页显式评价 1–5 分，可带原因与评论；同一次运行重复提交以最后一次为准",
        (
            "自述数据，只覆盖主动评价的运行，回收率低时代表性有限",
            "同一运行重复提交以最后一次为准",
            "rating >= 4 为正向，rating <= 2 为负向",
        ),
    ),
}


def test_catalog_has_seven_metrics_in_order() -> None:
    assert len(METRICS) == 7
    assert tuple(metric.id for metric in METRICS) == EXPECTED_ORDER


def test_metric_definitions_match_spec() -> None:
    """七条目录逐字段照 spec §4.5.1 的表。"""
    for metric in METRICS:
        (
            label,
            scope,
            unit,
            evidence,
            min_sample,
            supports_breakdown,
            definition,
            formula,
            source,
            evaluation,
            limitations,
        ) = EXPECTED[metric.id]
        assert metric.label == label, metric.id
        assert metric.scope is scope, metric.id
        assert metric.unit == unit, metric.id
        assert metric.evidence is evidence, metric.id
        assert metric.min_sample == min_sample, metric.id
        assert metric.supports_breakdown is supports_breakdown, metric.id
        assert metric.definition == definition, metric.id
        assert metric.formula == formula, metric.id
        assert metric.source == source, metric.id
        assert metric.evaluation == evaluation, metric.id
        assert metric.limitations == limitations, metric.id
        assert metric.supports_trend is True, metric.id


def test_offline_metrics_are_record_or_live_only() -> None:
    """离线三项只统计 record/live；证据等级是实测，限制里写明 replay 不算。"""
    offline = [metric for metric in METRICS if metric.scope is MetricScope.OFFLINE]
    assert [metric.id for metric in offline] == list(EXPECTED_ORDER[:3])
    for metric in offline:
        assert metric.evidence is EvidenceGrade.MEASURED, metric.id
        assert any("replay" in text for text in metric.limitations), metric.id
    assert "`record`/`live`" in offline[0].definition


def test_breakdown_supported_set() -> None:
    """supports_breakdown 只对四个在线指标为真，dimension 固定 ticket_kind。"""
    supported = {
        metric.id for metric in METRICS if metric.supports_breakdown
    }
    assert supported == {
        MetricId.TASK_COMPLETION,
        MetricId.CONVERSATION_EFFICIENCY,
        MetricId.COST,
        MetricId.USER_SATISFACTION,
    }
    for metric in METRICS:
        if metric.scope is MetricScope.OFFLINE:
            assert metric.supports_breakdown is False, metric.id
