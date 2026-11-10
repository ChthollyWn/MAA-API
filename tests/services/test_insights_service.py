"""``InsightsService`` 的聚合行为（M99 规格 §4.5.2–§4.5.4）。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓没有 pytest-asyncio）。
临时库来自 ``tests/services/conftest.py`` 的 ``retention_session_factory``；``now``
用 monkeypatch 固定在 ``NOW``，窗口边界与 delta 才能精确断言。

构造的数据覆盖：

- 离线：``record`` / ``live`` / ``replay`` 三种运行，``skipped`` / ``error`` 用例，
  以及上一窗口的旧运行；
- 工单：done / failed / cancelled / open、``ended_at`` 缺失的终态、上一窗口的 done；
- 对话效率：用户轮次只数 ``agent_inbox.user_message``，另有 ``agent_message.role=user``
  的干扰行；
- 成本：正常单币种、混入失败工单的运行、币种不一致；
- 满意度：正/负/中性评价、窗口外的旧评价、回收率分母的结束运行数。
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from maa_api.db.models import (
    AgentInbox,
    AgentMessage,
    AgentRun,
    AgentSession,
    EvalCaseResult,
    EvalRun,
    RunFeedback,
    Ticket,
)
from maa_api.domain.enums import (
    AgentRunStatus,
    EvalCaseStatus,
    EvalRunMode,
    MetricId,
    MetricScope,
)
from maa_api.services.insights import InsightsService
from maa_api.services.insights import service as service_module
from maa_api.services.insights.catalog import METRICS
from maa_api.services.insights.stats import wilson_interval

NOW = datetime(2026, 11, 10, 12, 0, 0)
WINDOW = 30

# 工单终态时间：11-05 到 11-09、上一窗口的 10-01。
D5 = NOW - timedelta(days=5)
D4 = NOW - timedelta(days=4)
D3 = NOW - timedelta(days=3)
D2 = NOW - timedelta(days=2)
D1 = NOW - timedelta(days=1)
OLD = NOW - timedelta(days=40)


@pytest.fixture
def service(retention_session_factory, monkeypatch):
    """固定时钟的 ``InsightsService``；库里还没有数据。"""
    monkeypatch.setattr(service_module, "utcnow", lambda: NOW)
    return InsightsService(retention_session_factory)


@pytest.fixture
def seeded(service, retention_session_factory):
    asyncio.run(_seed(retention_session_factory))
    return service


def test_empty_window_returns_none(service) -> None:
    """没有任何数据：7 张卡按顺序、值为空、样本 0，warnings 有说明。"""

    async def scenario():
        data = await service.overview(WINDOW)

        assert data.window_days == WINDOW
        assert data.generated_at == NOW
        assert [card.definition.id for card in data.items] == [
            metric.id for metric in METRICS
        ]
        for card in data.items:
            assert card.reading.value is None, card.definition.id
            assert card.reading.sample_size == 0, card.definition.id
            assert card.reading.ci_low is None, card.definition.id
            assert card.reading.ci_high is None, card.definition.id
            assert card.reading.delta is None, card.definition.id

        warnings = list(data.warnings)
        assert (
            "准确率、幻觉率、安全率还没有 record/live 模式的评测结果" in warnings
        )
        assert any("窗口内没有数据" in warning for warning in warnings)

        trend = await service.trend(MetricId.TASK_COMPLETION, WINDOW)
        assert trend.points == ()

        digest = await service.feedback_digest(WINDOW, limit=10)
        assert digest.total == 0
        assert digest.response_rate is None
        assert digest.reasons == ()
        assert digest.items == ()

        breakdown = await service.breakdown(MetricId.COST, WINDOW)
        assert breakdown.items == ()
        assert breakdown.truncated is False

    asyncio.run(scenario())


def test_replay_only_offline_metrics_are_empty(
    service, retention_session_factory
) -> None:
    """只有 replay 历史时离线主值为空，但 secondary 的 replay 计数照常显示。"""

    async def scenario():
        async with retention_session_factory() as db:
            run, cases = _eval_run(
                "eval-replay-only",
                EvalRunMode.REPLAY,
                D2,
                cases=[
                    ("rep-only-1", EvalCaseStatus.PASSED),
                    ("rep-only-2", EvalCaseStatus.PASSED),
                ],
                metrics=["accuracy"],
            )
            db.add(run)
            await db.flush()
            db.add_all(cases)
            await db.commit()

        data = await service.overview(WINDOW)
        accuracy = _card(data, MetricId.ACCURACY)
        assert accuracy.reading.value is None
        assert accuracy.reading.sample_size == 0
        assert _secondary(accuracy)["replay 用例数"].value == 2
        assert (
            "准确率、幻觉率、安全率还没有 record/live 模式的评测结果"
            in data.warnings
        )

    asyncio.run(scenario())


def test_service_catalog_returns_metrics(service) -> None:
    async def scenario():
        catalog = await service.catalog()
        assert tuple(catalog) == METRICS

    asyncio.run(scenario())


def test_offline_metrics_ignore_replay(seeded) -> None:
    """离线三项只统计 record/live；replay 只出现在准确率的 secondary 计数里。"""

    async def scenario():
        data = await seeded.overview(WINDOW)

        accuracy = _card(data, MetricId.ACCURACY)
        assert accuracy.reading.value == pytest.approx(2 / 3)
        assert accuracy.reading.sample_size == 3
        assert accuracy.reading.ci_low == pytest.approx(wilson_interval(2, 3)[0])
        assert accuracy.reading.ci_high == pytest.approx(wilson_interval(2, 3)[1])
        assert accuracy.reading.delta == pytest.approx(2 / 3 - 1 / 4)
        assert accuracy.reading.updated_at == D1
        secondary = _secondary(accuracy)
        assert secondary["评测用例数"].value == 3
        assert secondary["replay 用例数"].value == 2

        hallucination = _card(data, MetricId.HALLUCINATION_RATE)
        assert hallucination.reading.value == pytest.approx(0.5)
        assert hallucination.reading.sample_size == 2
        assert hallucination.reading.delta is None
        secondary = _secondary(hallucination)
        assert secondary["被判分场景数"].value == 2
        assert secondary["无依据断言数"].value == 1

        safety = _card(data, MetricId.SAFETY_RATE)
        assert safety.reading.value == pytest.approx(3 / 4)
        assert safety.reading.sample_size == 4
        assert safety.reading.delta is None

        warnings = list(data.warnings)
        assert "准确率样本量不足（3/10）" in warnings
        assert "幻觉率样本量不足（2/5）" in warnings
        assert "安全率样本量不足（4/6）" in warnings

    asyncio.run(scenario())


def test_safety_secondary_by_tags(seeded) -> None:
    """安全 secondary 按 tags 分攻击/对照场景的通过计数。"""

    async def scenario():
        data = await seeded.overview(WINDOW)
        safety = _card(data, MetricId.SAFETY_RATE)
        secondary = _secondary(safety)

        attack = secondary["攻击场景通过"]
        assert attack.value == 1
        assert attack.unit == "count"
        assert attack.sample_size == 2

        control = secondary["对照场景通过"]
        assert control.value == 2
        assert control.unit == "count"
        assert control.sample_size == 2

    asyncio.run(scenario())


def test_completion_rate_and_verified_secondary(seeded) -> None:
    """完成率分母只含三个终态；done 的验证方式按 machine/verifier/waiver 分区。"""

    async def scenario():
        data = await seeded.overview(WINDOW)
        completion = _card(data, MetricId.TASK_COMPLETION)

        assert completion.definition.scope is MetricScope.ONLINE
        assert completion.reading.value == pytest.approx(3 / 5)
        assert completion.reading.sample_size == 5
        assert completion.reading.ci_low == pytest.approx(wilson_interval(3, 5)[0])
        assert completion.reading.ci_high == pytest.approx(wilson_interval(3, 5)[1])
        assert completion.reading.delta == pytest.approx(3 / 5 - 1)
        # ended_at 为空的终态工单用 updated_at 代替（t-2）。
        assert completion.reading.updated_at == D1

        secondary = _secondary(completion)
        # t-1 machine、t-2 verifier、t-7 waiver：机器验证子集含 machine/verifier，
        # 验证员放行是该子集里的 verifier 部分，用户放行单列。
        assert secondary["机器验证通过"].value == pytest.approx(2 / 3)
        assert secondary["验证员放行"].value == pytest.approx(1 / 3)
        assert secondary["用户放行（waiver）"].value == pytest.approx(1 / 3)
        for label in ("机器验证通过", "验证员放行", "用户放行（waiver）"):
            assert secondary[label].sample_size == 3, label
            assert secondary[label].unit == "ratio", label
        # min_sample 只影响 warnings 文案，不改变数值（spec §4.5.2）。
        assert "任务完成率样本量不足（5/20）" in data.warnings

    asyncio.run(scenario())


def test_efficiency_medians(seeded) -> None:
    """用户轮次只数 ``agent_inbox.user_message``；没有用户消息的 done 工单不进样本。"""

    async def scenario():
        data = await seeded.overview(WINDOW)
        efficiency = _card(data, MetricId.CONVERSATION_EFFICIENCY)

        # t-1 = 2、t-2 = 4；t-7 没有用户消息被排除；agent_message 的
        # role=user 干扰行不得计数，否则 t-1 会变成 5 条。
        assert efficiency.reading.value == pytest.approx(3.0)
        assert efficiency.reading.sample_size == 2
        assert efficiency.reading.ci_low is None
        assert efficiency.reading.ci_high is None
        assert efficiency.reading.delta == pytest.approx(3.0 - 1.0)

        secondary = _secondary(efficiency)
        assert secondary["每单运行次数中位"].value == pytest.approx(1.5)
        assert secondary["每单模型调用中位"].value == pytest.approx(4.5)
        assert secondary["每单 token 中位"].value == pytest.approx(97.5)
        for item in secondary.values():
            assert item.unit == "count"
            assert item.sample_size == 2

    asyncio.run(scenario())


def test_cost_average_and_secondary(seeded) -> None:
    """成本主值 = done 工单关联运行的成本和 / done 工单数；非 done 的运行不算。"""

    async def scenario():
        data = await seeded.overview(WINDOW)
        cost = _card(data, MetricId.COST)

        # t-1 的 1000+500、t-2 的 300、t-7 的 200；t-3（failed）的 999 不计。
        assert cost.reading.value == pytest.approx(2000 / 3)
        assert cost.reading.sample_size == 3
        assert cost.reading.delta == pytest.approx(2000 / 3 - 500)
        assert cost.reading.updated_at == D1

        secondary = _secondary(cost)
        assert secondary["窗口总成本"].value == pytest.approx(2000)
        assert secondary["窗口总成本"].unit == "micros"
        assert secondary["窗口总成本"].sample_size == 4
        assert secondary["每完成工单 token"].value == pytest.approx(235 / 3)
        assert secondary["缓存命中率"].value == pytest.approx(45 / 160)

    asyncio.run(scenario())


def test_cost_currency_mismatch_nulls_value(
    seeded, retention_session_factory
) -> None:
    """窗口内多于一种币种：主值置空、warnings 一条、secondary 分币种。"""

    async def scenario():
        async with retention_session_factory() as db:
            db.add(
                _run(
                    "run-extra",
                    ticket_id="t-2",
                    session_id="sess-b",
                    status=AgentRunStatus.SUCCEEDED,
                    finished_at=D1,
                    cost_micros=100,
                    currency="EUR",
                )
            )
            await db.commit()

        data = await seeded.overview(WINDOW)
        cost = _card(data, MetricId.COST)

        assert cost.reading.value is None
        assert cost.reading.sample_size == 3
        assert "成本存在多种币种，未合并" in data.warnings
        secondary = _secondary(cost)
        assert secondary["USD"].value == pytest.approx(2000)
        assert secondary["USD"].unit == "micros"
        assert secondary["USD"].sample_size == 4
        assert secondary["EUR"].value == pytest.approx(100)
        assert secondary["EUR"].sample_size == 1

    asyncio.run(scenario())


def test_cost_unknown_currency_is_separate_group(
    seeded, retention_session_factory
) -> None:
    """currency 为空的行按「未知」单列，与真实币种一起视为多币种。"""

    async def scenario():
        async with retention_session_factory() as db:
            db.add(
                _run(
                    "run-unknown-currency",
                    ticket_id="t-1",
                    session_id="sess-a",
                    status=AgentRunStatus.SUCCEEDED,
                    finished_at=D2,
                    cost_micros=50,
                    currency=None,
                )
            )
            await db.commit()

        data = await seeded.overview(WINDOW)
        cost = _card(data, MetricId.COST)

        assert cost.reading.value is None
        assert "成本存在多种币种，未合并" in data.warnings
        secondary = _secondary(cost)
        assert secondary["未知"].value == pytest.approx(50)
        assert secondary["未知"].sample_size == 1
        assert secondary["USD"].value == pytest.approx(2000)

    asyncio.run(scenario())


def test_satisfaction_positive_rate_and_response_rate(seeded) -> None:
    """正向率 = rating>=4 的比例；回收率分母是窗口内结束的运行数。"""

    async def scenario():
        data = await seeded.overview(WINDOW)
        satisfaction = _card(data, MetricId.USER_SATISFACTION)

        assert satisfaction.reading.value == pytest.approx(0.5)
        assert satisfaction.reading.sample_size == 4
        assert satisfaction.reading.ci_low == pytest.approx(wilson_interval(2, 4)[0])
        assert satisfaction.reading.ci_high == pytest.approx(wilson_interval(2, 4)[1])
        assert satisfaction.reading.delta == pytest.approx(0.5 - 1.0)
        assert satisfaction.reading.updated_at == D1

        secondary = _secondary(satisfaction)
        assert secondary["评价数"].value == 4
        assert secondary["评价数"].sample_size == 4
        # 窗口内结束的运行：run-a1/a2、run-b1、run-c1、run-d1 = 5。
        assert secondary["回收率"].value == pytest.approx(4 / 5)
        assert secondary["回收率"].sample_size == 5
        assert secondary["负向率"].value == pytest.approx(1 / 4)

    asyncio.run(scenario())


def test_trend_by_utc_day(seeded) -> None:
    """趋势按 UTC 日分桶，只返回有数据的天；replay 不进入离线趋势。"""

    async def scenario():
        completion = await seeded.trend(MetricId.TASK_COMPLETION, WINDOW)
        assert completion.scope is MetricScope.ONLINE
        assert completion.unit == "ratio"
        assert [(point.date, point.value) for point in completion.points] == [
            ("2026-11-05", 1.0),
            ("2026-11-06", 0.0),
            ("2026-11-07", 0.0),
            ("2026-11-08", 1.0),
            ("2026-11-09", 1.0),
        ]
        assert [point.sample_size for point in completion.points] == [1, 1, 1, 1, 1]

        accuracy = await seeded.trend(MetricId.ACCURACY, WINDOW)
        # 11-07 只有 replay 运行，不进入离线趋势。
        assert [(point.date, point.value) for point in accuracy.points] == [
            ("2026-11-08", 0.5),
            ("2026-11-09", 1.0),
        ]
        assert accuracy.points[0].sample_size == 2
        assert accuracy.points[0].ci_low is not None

        efficiency = await seeded.trend(MetricId.CONVERSATION_EFFICIENCY, WINDOW)
        assert [(point.date, point.value) for point in efficiency.points] == [
            ("2026-11-08", 2.0),
            ("2026-11-09", 4.0),
        ]

    asyncio.run(scenario())


def test_breakdown_by_ticket_kind(seeded) -> None:
    """四个在线指标按工单种类分组；不支持分组的指标抛错。"""

    async def scenario():
        completion = await seeded.breakdown(MetricId.TASK_COMPLETION, WINDOW)
        assert completion.dimension == "ticket_kind"
        assert completion.truncated is False
        values = {item.key: (item.value, item.sample_size) for item in completion.items}
        assert values["analysis"] == (pytest.approx(1 / 3), 3)
        assert values["daily"] == (pytest.approx(1.0), 2)

        efficiency = await seeded.breakdown(
            MetricId.CONVERSATION_EFFICIENCY, WINDOW
        )
        # t-7 没有用户消息，analysis 没有样本。
        assert [item.key for item in efficiency.items] == ["daily"]
        assert efficiency.items[0].value == pytest.approx(3.0)

        cost = await seeded.breakdown(MetricId.COST, WINDOW)
        cost_values = {item.key: item.value for item in cost.items}
        assert cost_values["daily"] == pytest.approx(900)
        assert cost_values["analysis"] == pytest.approx(200)

        satisfaction = await seeded.breakdown(MetricId.USER_SATISFACTION, WINDOW)
        satisfaction_values = {
            item.key: (item.value, item.sample_size) for item in satisfaction.items
        }
        assert satisfaction_values["daily"] == (pytest.approx(1.0), 2)
        assert satisfaction_values["analysis"] == (pytest.approx(0.0), 2)

        with pytest.raises(ValueError):
            await seeded.breakdown(MetricId.ACCURACY, WINDOW)

    asyncio.run(scenario())


def test_window_boundaries_are_half_open(
    seeded, retention_session_factory
) -> None:
    """窗口左闭右开：恰好落在起点的行进入，恰好落在终点的行不进入。"""

    async def scenario():
        async with retention_session_factory() as db:
            db.add_all(
                [
                    Ticket(
                        id="t-start",
                        title="边界起点",
                        kind="daily",
                        status="done",
                        origin="user",
                        created_at=NOW - timedelta(days=30),
                        updated_at=NOW - timedelta(days=30),
                        ended_at=NOW - timedelta(days=30),
                    ),
                    Ticket(
                        id="t-end",
                        title="边界终点",
                        kind="daily",
                        status="done",
                        origin="user",
                        created_at=NOW,
                        updated_at=NOW,
                        ended_at=NOW,
                    ),
                ]
            )
            edge_runs = [
                _eval_run(
                    "eval-start",
                    EvalRunMode.RECORD,
                    NOW - timedelta(days=30),
                    cases=[("edge-acc-start", EvalCaseStatus.PASSED)],
                    metrics=["accuracy"],
                ),
                _eval_run(
                    "eval-end",
                    EvalRunMode.RECORD,
                    NOW,
                    cases=[("edge-acc-end", EvalCaseStatus.PASSED)],
                    metrics=["accuracy"],
                ),
            ]
            db.add_all([run for run, _ in edge_runs])
            await db.flush()
            db.add_all([case for _, cases in edge_runs for case in cases])
            await db.commit()

        data = await seeded.overview(WINDOW)
        completion = _card(data, MetricId.TASK_COMPLETION)
        assert completion.reading.sample_size == 6
        assert completion.reading.value == pytest.approx(4 / 6)

        accuracy = _card(data, MetricId.ACCURACY)
        assert accuracy.reading.sample_size == 4
        assert accuracy.reading.value == pytest.approx(3 / 4)

    asyncio.run(scenario())


def test_feedback_digest(seeded) -> None:
    """反馈摘要：正/负/中性计数、回收率、原因排序与最近评价列表。"""

    async def scenario():
        digest = await seeded.feedback_digest(WINDOW, limit=2)

        assert digest.window_days == WINDOW
        assert digest.total == 4
        assert digest.positive == 2
        assert digest.negative == 1
        assert digest.neutral == 1
        assert digest.response_rate == pytest.approx(4 / 5)
        assert [(item.reason, item.count) for item in digest.reasons] == [
            ("slow", 2),
            ("inaccurate", 1),
            ("incomplete", 1),
        ]

        assert len(digest.items) == 2
        first = digest.items[0]
        assert first.run_id == "run-a1"
        assert first.ticket_id == "t-1"
        assert first.agent_name == "daily_ops"
        assert first.rating == 5
        assert first.reasons == ("slow",)
        assert first.created_at == D1
        assert digest.items[1].run_id == "run-b1"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 构造 helper
# ---------------------------------------------------------------------------
def _card(data, metric: MetricId):
    matches = [
        card for card in data.items if card.definition.id is metric
    ]
    assert len(matches) == 1, metric
    return matches[0]


def _secondary(card) -> dict:
    return {item.label: item for item in card.reading.secondary}


def _run(
    run_id: str,
    *,
    ticket_id: str,
    session_id: str | None,
    status: AgentRunStatus,
    finished_at: datetime | None,
    steps: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
    cost_micros: int = 0,
    currency: str | None = None,
) -> AgentRun:
    return AgentRun(
        id=run_id,
        root_run_id=run_id,
        session_id=session_id,
        ticket_id=ticket_id,
        agent_name="daily_ops",
        kind="worker_turn",
        status=status,
        boot_id="boot-test",
        started_at=finished_at or NOW,
        finished_at=finished_at,
        steps=steps,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        cost_micros=cost_micros,
        currency=currency,
    )


def _eval_run(
    run_id: str,
    mode: EvalRunMode,
    finished_at: datetime,
    *,
    cases: list[tuple[str, EvalCaseStatus]],
    metrics: list[str] | None = None,
    tags: list[str] | None = None,
) -> tuple[EvalRun, list[EvalCaseResult]]:
    """建一次评测；``cases`` 是 ``(scenario, status)``，metrics/tags 可统一覆盖。"""
    run = EvalRun(
        id=run_id,
        suite="capability",
        mode=mode,
        started_at=finished_at - timedelta(minutes=3),
        finished_at=finished_at,
    )
    case_rows = [
        EvalCaseResult(
            eval_run_id=run_id,
            scenario=scenario,
            title=scenario,
            suite="capability",
            agent="legacy_chat",
            metrics=list(metrics or []),
            tags=list(tags or []),
            status=status,
            attempt=1,
            variant="target",
        )
        for scenario, status in cases
    ]
    return run, case_rows


async def _seed(factory) -> None:
    """一份覆盖七项指标、上一窗口与空值边界的固定数据。"""
    tickets = [
        Ticket(
            id="t-1",
            title="日常一",
            kind="daily",
            status="done",
            origin="user",
            result={"verification": {"method": "machine"}},
            created_at=D2,
            updated_at=D2,
            ended_at=D2,
        ),
        Ticket(
            id="t-2",
            title="日常二",
            kind="daily",
            status="done",
            origin="user",
            result={"verification": {"method": "verifier"}},
            created_at=D1,
            updated_at=D1,
            ended_at=None,
        ),
        Ticket(
            id="t-3",
            title="分析失败",
            kind="analysis",
            status="failed",
            origin="user",
            created_at=D3,
            updated_at=D3,
            ended_at=D3,
        ),
        Ticket(
            id="t-4",
            title="分析取消",
            kind="analysis",
            status="cancelled",
            origin="user",
            created_at=D4,
            updated_at=D4,
            ended_at=D4,
        ),
        Ticket(
            id="t-5",
            title="未结束",
            kind="copilot",
            status="open",
            origin="user",
            created_at=NOW - timedelta(hours=1),
            updated_at=NOW - timedelta(hours=1),
        ),
        Ticket(
            id="t-6",
            title="上一窗口的完成",
            kind="daily",
            status="done",
            origin="user",
            created_at=OLD,
            updated_at=OLD,
            ended_at=OLD,
        ),
        Ticket(
            id="t-7",
            title="分析完成",
            kind="analysis",
            status="done",
            origin="user",
            result={"verification": {"method": "waiver"}},
            created_at=D5,
            updated_at=D5,
            ended_at=D5,
        ),
    ]
    sessions = [
        AgentSession(
            id="sess-a", model="test-model", kind="worker", ticket_id="t-1"
        ),
        AgentSession(
            id="sess-b", model="test-model", kind="worker", ticket_id="t-2"
        ),
        AgentSession(
            id="sess-d", model="test-model", kind="worker", ticket_id="t-7"
        ),
        AgentSession(
            id="sess-e", model="test-model", kind="worker", ticket_id="t-6"
        ),
    ]
    inbox = [
        _inbox("sess-a", "user_message", D2 - timedelta(minutes=20)),
        _inbox("sess-a", "user_message", D2 - timedelta(minutes=10)),
        _inbox("sess-a", "report", D2 - timedelta(minutes=5)),
        *[
            _inbox("sess-b", "user_message", D1 - timedelta(minutes=n))
            for n in range(1, 5)
        ],
        _inbox("sess-e", "user_message", OLD + timedelta(minutes=5)),
    ]
    # 干扰行：agent_message.role=user 不能计入用户轮次（汇报/事件卡片也这样落库）。
    messages = [
        AgentMessage(
            session_id="sess-a",
            seq=n,
            role="user",
            content=f"干扰消息 {n}",
            created_at=D2,
        )
        for n in (1, 2, 3)
    ]
    runs = [
        _run(
            "run-a1",
            ticket_id="t-1",
            session_id="sess-a",
            status=AgentRunStatus.SUCCEEDED,
            finished_at=D2,
            steps=5,
            prompt_tokens=100,
            completion_tokens=50,
            cached_tokens=40,
            cost_micros=1000,
            currency="USD",
        ),
        _run(
            "run-a2",
            ticket_id="t-1",
            session_id="sess-a",
            status=AgentRunStatus.SUCCEEDED,
            finished_at=D3,
            steps=3,
            prompt_tokens=20,
            completion_tokens=10,
            cached_tokens=5,
            cost_micros=500,
            currency="USD",
        ),
        _run(
            "run-b1",
            ticket_id="t-2",
            session_id="sess-b",
            status=AgentRunStatus.FAILED,
            finished_at=D1,
            steps=1,
            prompt_tokens=10,
            completion_tokens=5,
            cost_micros=300,
            currency="USD",
        ),
        _run(
            "run-c1",
            ticket_id="t-3",
            session_id=None,
            status=AgentRunStatus.INTERRUPTED,
            finished_at=D3,
            steps=7,
            prompt_tokens=70,
            completion_tokens=30,
            cost_micros=999,
            currency="EUR",
        ),
        _run(
            "run-d1",
            ticket_id="t-7",
            session_id="sess-d",
            status=AgentRunStatus.SUCCEEDED,
            finished_at=D5,
            steps=2,
            prompt_tokens=30,
            completion_tokens=10,
            cost_micros=200,
            currency="USD",
        ),
        _run(
            "run-e1",
            ticket_id="t-6",
            session_id="sess-e",
            status=AgentRunStatus.SUCCEEDED,
            finished_at=OLD,
            steps=1,
            prompt_tokens=10,
            cost_micros=500,
            currency="USD",
        ),
        _run(
            "run-open",
            ticket_id="t-5",
            session_id=None,
            status=AgentRunStatus.RUNNING,
            finished_at=None,
        ),
    ]
    feedback = [
        _feedback(1, "run-a1", 5, ["slow"], D1),
        _feedback(2, "run-b1", 4, ["incomplete", "slow"], D2),
        _feedback(3, "run-d1", 2, ["inaccurate"], D3),
        _feedback(4, "run-c1", 3, [], D4, comment="还行"),
        _feedback(5, "run-a2", 5, [], OLD),
    ]
    eval_specs = [
        _eval_run(
            "eval-record-1",
            EvalRunMode.RECORD,
            D2,
            cases=[
                ("acc-1", EvalCaseStatus.PASSED),
                ("acc-2", EvalCaseStatus.FAILED),
                ("acc-3", EvalCaseStatus.SKIPPED),
                ("acc-4", EvalCaseStatus.ERROR),
            ],
            metrics=["accuracy"],
        ),
        _eval_run(
            "eval-record-hall",
            EvalRunMode.RECORD,
            D2,
            cases=[
                ("hall-1", EvalCaseStatus.FAILED),
                ("hall-2", EvalCaseStatus.PASSED),
            ],
            metrics=["hallucination_rate"],
            tags=["groundedness"],
        ),
        _eval_run(
            "eval-record-safety",
            EvalRunMode.RECORD,
            D2,
            cases=[
                ("attack-1", EvalCaseStatus.PASSED),
                ("attack-2", EvalCaseStatus.FAILED),
            ],
            metrics=["safety_rate"],
            tags=["safety-attack"],
        ),
        _eval_run(
            "eval-record-control",
            EvalRunMode.RECORD,
            D2,
            cases=[
                ("control-1", EvalCaseStatus.PASSED),
                ("control-2", EvalCaseStatus.PASSED),
            ],
            metrics=["safety_rate"],
            tags=["safety-control"],
        ),
        _eval_run(
            "eval-replay-1",
            EvalRunMode.REPLAY,
            D3,
            cases=[
                ("rep-acc-1", EvalCaseStatus.PASSED),
                ("rep-acc-2", EvalCaseStatus.PASSED),
            ],
            metrics=["accuracy"],
        ),
        _eval_run(
            "eval-live-1",
            EvalRunMode.LIVE,
            D1,
            cases=[("live-acc-1", EvalCaseStatus.PASSED)],
            metrics=["accuracy"],
        ),
        _eval_run(
            "eval-record-old",
            EvalRunMode.RECORD,
            OLD,
            cases=[
                ("old-acc-1", EvalCaseStatus.PASSED),
                ("old-acc-2", EvalCaseStatus.FAILED),
                ("old-acc-3", EvalCaseStatus.FAILED),
                ("old-acc-4", EvalCaseStatus.FAILED),
            ],
            metrics=["accuracy"],
        ),
        # metrics 为空的旧场景不进入任何离线指标（spec §4.5.5）。
        _eval_run(
            "eval-record-unlabeled",
            EvalRunMode.RECORD,
            D2,
            cases=[("unlabeled-1", EvalCaseStatus.PASSED)],
        ),
    ]

    async with factory() as db:
        db.add_all(tickets)
        await db.flush()
        db.add_all(sessions)
        await db.flush()
        worker_sessions = {
            "t-1": "sess-a",
            "t-2": "sess-b",
            "t-6": "sess-e",
            "t-7": "sess-d",
        }
        for ticket in tickets:
            session_id = worker_sessions.get(ticket.id)
            if session_id is not None:
                ticket.worker_session_id = session_id
        db.add_all(inbox)
        db.add_all(messages)
        db.add_all(runs)
        db.add_all(feedback)
        await db.flush()
        db.add_all([run for run, _ in eval_specs])
        await db.flush()
        db.add_all([case for _, cases in eval_specs for case in cases])
        await db.commit()


def _inbox(session_id: str, kind: str, created_at: datetime) -> AgentInbox:
    return AgentInbox(
        session_id=session_id,
        kind=kind,
        status="consumed",
        created_at=created_at,
    )


def _feedback(
    feedback_id: int,
    run_id: str,
    rating: int,
    reasons: list[str],
    created_at: datetime,
    *,
    comment: str | None = None,
) -> RunFeedback:
    return RunFeedback(
        id=feedback_id,
        run_id=run_id,
        rating=rating,
        reasons=reasons,
        comment=comment,
        created_at=created_at,
        updated_at=created_at,
    )
