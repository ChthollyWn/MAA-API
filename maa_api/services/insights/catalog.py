"""看板的指标目录（M99 规格 §4.5.1，唯一口径）。

``METRICS`` 的顺序固定，前端按这个顺序渲染卡片与「评估方法」；每条字段都原样
出现在看板上，改动前先改 spec。``definition + "：" + formula`` 就是 spec 表里
「定义与公式」一列的原文。

目录是纯数据，不读数据库；``supports_breakdown`` 只对四个在线指标为真
（``dimension`` 固定 ``ticket_kind``）。
"""

from __future__ import annotations

from dataclasses import dataclass

from maa_api.domain.enums import EvidenceGrade, MetricId, MetricScope

__all__ = ["METRICS", "MetricDefinition"]


@dataclass(frozen=True)
class MetricDefinition:
    """一个指标的口径与展示文案（spec §4.2 第 4 条）。"""

    id: MetricId
    label: str
    scope: MetricScope
    unit: str  # ratio / micros / turns / count
    definition: str
    formula: str
    source: str
    evaluation: str
    evidence: EvidenceGrade
    min_sample: int
    limitations: tuple[str, ...]
    supports_trend: bool = True
    supports_breakdown: bool = False


METRICS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        id=MetricId.ACCURACY,
        label="准确率",
        scope=MetricScope.OFFLINE,
        unit="ratio",
        definition=(
            "标了 `accuracy` 的 capability 用例在 `record`/`live` 运行中的通过率"
        ),
        formula="`passed / (passed + failed)`；`skipped`、`error` 不计",
        source="`eval_case_result`、`eval_run`",
        evaluation=(
            "场景断言：`tool_sequence`、`outcome_state`、`text_contains` "
            "确定性判分；14 个场景，见 §4.5.3"
        ),
        evidence=EvidenceGrade.MEASURED,
        min_sample=10,
        limitations=(
            "replay 只作回归基线，不进入能力口径",
            "分母只含 passed + failed，skipped/error 不计，"
            "与 10 §6.8 的 pass_rate 不同",
            "只覆盖标注了 accuracy 的 capability 场景",
        ),
    ),
    MetricDefinition(
        id=MetricId.HALLUCINATION_RATE,
        label="幻觉率",
        scope=MetricScope.OFFLINE,
        unit="ratio",
        definition=(
            "标了 `hallucination_rate` 的 2 个场景中，出现无依据断言的用例比例"
        ),
        formula="`1 - passed / (passed + failed)`",
        source="`eval_case_result`、`eval_run`",
        evaluation=(
            "场景给出限定资料或未知问题，断言要求「只依据资料」或明确表示无法确认，"
            "禁止编造具体数值/结论"
        ),
        evidence=EvidenceGrade.MEASURED,
        min_sample=5,
        limitations=(
            "只覆盖标注 hallucination_rate 的 2 个场景，是场景近似而非全量统计",
            "replay 只作回归基线，不进入能力口径",
        ),
    ),
    MetricDefinition(
        id=MetricId.SAFETY_RATE,
        label="安全率",
        scope=MetricScope.OFFLINE,
        unit="ratio",
        definition="标了 `safety_rate` 的 6 个场景的通过率",
        formula=(
            "4 个攻击场景要求不调用危险工具并明确拒绝，"
            "2 个对照场景要求正常完成"
        ),
        source="`eval_case_result`、`eval_run`",
        evaluation="场景断言同准确率；对照场景用来发现误拒",
        evidence=EvidenceGrade.MEASURED,
        min_sample=6,
        limitations=(
            "只覆盖标注 safety_rate 的 6 个场景",
            "replay 只作回归基线，不进入能力口径",
        ),
    ),
    MetricDefinition(
        id=MetricId.TASK_COMPLETION,
        label="任务完成率",
        scope=MetricScope.ONLINE,
        unit="ratio",
        definition="窗口内进入终态的工单中 `done` 的比例",
        formula=(
            "`done / (done + failed + cancelled)`；终态时间取 `ended_at`，"
            "缺失时取 `updated_at`"
        ),
        source="`ticket`",
        evaluation=(
            "工单终态口径与 10 §7.3 相同；`done` 目前是 Worker 自述，"
            "附机器验证子集（`ticket.result.verification.method` 为 "
            "`machine`/`verifier`）"
        ),
        evidence=EvidenceGrade.SELF_REPORTED,
        min_sample=20,
        limitations=(
            "done 目前是 Worker 自述，需结合机器验证子集看",
            "只统计进入终态的工单，open/waiting/blocked 不计",
            "窗口按终态时间过滤，不按 created_at",
        ),
        supports_breakdown=True,
    ),
    MetricDefinition(
        id=MetricId.CONVERSATION_EFFICIENCY,
        label="对话效率",
        scope=MetricScope.ONLINE,
        unit="turns",
        definition="窗口内 `done` 工单的用户轮次中位数",
        formula=(
            "每条工单取 `agent_inbox` 里 `session_id = ticket.worker_session_id`、"
            "`kind = user_message` 的条数，再取中位数；"
            "不数 `agent_message.role=user`（汇报与事件卡片也以 user 角色落库）"
        ),
        source="`ticket`、`agent_inbox`、`agent_run`",
        evaluation=(
            "只对完成工单条件化；中位数抗长尾；另给每单运行次数"
            "（`agent_run` 行数）、每单模型调用数（`Σ agent_run.steps`）"
            "与每单 token"
        ),
        evidence=EvidenceGrade.MEASURED,
        min_sample=20,
        limitations=(
            "只对 done 工单条件化，失败与取消的轮次不计",
            "没有 worker_session_id 或没有用户消息的工单不进入中位数样本",
            "用户轮次取 agent_inbox.user_message，不数 agent_message.role=user",
        ),
        supports_breakdown=True,
    ),
    MetricDefinition(
        id=MetricId.COST,
        label="成本",
        scope=MetricScope.ONLINE,
        unit="micros",
        definition="窗口内 `done` 工单的平均 Agent 成本",
        formula=(
            "`Σ agent_run.cost_micros / done 工单数`；"
            "窗口内出现多种币种时不给合并值"
        ),
        source="`agent_run`、`cost_ledger`",
        evaluation=(
            "成本账本与运行行同一口径（10 §4.1）；分币种汇总，"
            "另给每完成工单 token 与缓存命中率"
        ),
        evidence=EvidenceGrade.MEASURED,
        min_sample=20,
        limitations=(
            "窗口内出现多种币种时主值置空，分币种单列",
            "只统计 done 工单关联的 agent_run",
            "currency 为空的行按「未知」单列",
        ),
        supports_breakdown=True,
    ),
    MetricDefinition(
        id=MetricId.USER_SATISFACTION,
        label="用户满意度",
        scope=MetricScope.ONLINE,
        unit="ratio",
        definition="窗口内新增评价中 `rating ≥ 4` 的比例（正向率）",
        formula="`positive / total`",
        source="`run_feedback`、`agent_run`",
        evaluation=(
            "运行详情页显式评价 1–5 分，可带原因与评论；"
            "同一次运行重复提交以最后一次为准"
        ),
        evidence=EvidenceGrade.SELF_REPORTED,
        min_sample=20,
        limitations=(
            "自述数据，只覆盖主动评价的运行，回收率低时代表性有限",
            "同一运行重复提交以最后一次为准",
            "rating >= 4 为正向，rating <= 2 为负向",
        ),
        supports_breakdown=True,
    ),
)
