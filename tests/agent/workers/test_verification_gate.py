"""M87 T2：完成闸门的判定顺序与用户放行（规格 §4.5.1–§4.5.3、§6）。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库由 ``tests/fakes/v3_db.py`` 的 ``make_session_factory`` 建。``_world()``
直接写工单、Worker 会话与运行、导航员子会话与子运行、审计、流水线、作业尝试、
验证员运行与提问卡，只有被测的 :class:`VerificationGate` 是真实实现。

钉住的硬约束：

- 只统计本工单 Worker 自己的写设备审计：导航员子代理的点击、只读审计与错绑会话
  都不算工单效果。
- 机器证据只认「最后一次 ``submit_pipeline`` 之后全部 ``completed``」与「最近一次
  尝试 ``success=true`` 且 ``judged_by="core"``」；``running`` 先拒且不委派。
- 验证员结论只认最后一次写设备操作之后结束的、``passed`` 且把握不低于 0.7 的；
  降级结论算一次失败。两次失败后只能 `verification_exhausted`，用户放行另算。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from maa_api.agent.workers.verification import (
    MAX_CONTEXT_CHARS,
    MAX_EVIDENCE,
    VERIFY_MAX_ROUNDS,
    VERIFY_MIN_CONFIDENCE,
    WAIVER_OPTION,
    VerificationGate,
)
from maa_api.db.models import (
    AgentAudit,
    AgentQuestion,
    AgentRun,
    AgentSession,
    Pipeline,
    ResourceAsset,
    Ticket,
)
from tests.fakes.v3_db import make_session_factory

#: 固定时钟：2026-11-20 08:00 UTC（naive），全部时间判断不随真实时间漂移。
BASE = datetime(2026, 11, 20, 8, 0, 0)
TICKET_ID = "ticket-1"
WORKER_SESSION_ID = "session-worker"
WORKER_RUN_ID = "run-worker"
NAV_SESSION_ID = "session-nav"
NAV_RUN_ID = "run-nav"
VERIFIER_SESSION_ID = "session-verifier"
OTHER_SESSION_ID = "session-other"
CLAIM = "日常已做完，理智清空"

#: 拒绝结果恰好是这五个键（规格 §4.3）。
REJECT_KEYS = {"accepted", "reason", "message", "evidence", "delegate"}
#: ``report_details()`` 只可能带这四个键（规格 §4.1）。
DETAIL_KEYS = {"method", "verdict_run_id", "question_id", "evidence"}


def _iso(value: Any) -> str | None:
    """把尝试记录里的时间写成生产同款 ISO 字符串（``attempt.py`` 的 ``_iso``）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.isoformat()
    return str(value)


def _attempt(index: int, item: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": f"attempt-{index + 1}",
        "session_id": WORKER_SESSION_ID,
        "pipeline_id": None,
        "recognition_pipeline_id": None,
        "exit_pipeline_id": None,
        "outcome": "clear",
        "judged_by": "core",
        "counted": True,
        "success": True,
        "attempt_number": index + 1,
        "stage_code": "1-7",
        "started_at": BASE + timedelta(minutes=index),
        "finished_at": BASE + timedelta(minutes=index, seconds=30),
    }
    row.update(item)
    return {
        **row,
        "started_at": _iso(row["started_at"]),
        "finished_at": _iso(row["finished_at"]),
    }


def _world(
    tmp_path: Path,
    *,
    worker_session: bool = True,
    audits: tuple[dict[str, Any], ...] = (),
    pipelines: tuple[dict[str, Any], ...] = (),
    attempts: tuple[dict[str, Any], ...] = (),
    verdicts: tuple[dict[str, Any], ...] = (),
    questions: tuple[dict[str, Any], ...] = (),
) -> tuple[VerificationGate, str, str]:
    """建一个临时库并写好一整张工单的现场；返回 ``(gate, ticket_id, worker_session_id)``。

    ``worker_session=False`` 时 Worker 会话是子代理会话（``parent_session_id`` 非空），
    用来钉「汇报来自别的会话」的错绑。``audits`` 里的 ``session_id`` / ``agent_name`` /
    ``run_id`` 默认是本工单的 Worker、Worker 运行与 ``daily_ops``。
    """

    async def build():
        tmp_path.mkdir(parents=True, exist_ok=True)
        _engine, factory = await make_session_factory(tmp_path / "m87-gate.db")
        async with factory() as db:
            ticket = Ticket(id=TICKET_ID, title="日常", kind="daily_ops", origin="user")
            db.add(ticket)
            await db.flush()
            if not worker_session:
                db.add(
                    AgentSession(
                        id="session-orchestrator", model="test-model", kind="orchestrator"
                    )
                )
                await db.flush()
            worker = AgentSession(
                id=WORKER_SESSION_ID,
                model="test-model",
                kind="worker",
                ticket_id=TICKET_ID,
                parent_session_id=None if worker_session else "session-orchestrator",
            )
            db.add(worker)
            db.add(
                AgentSession(
                    id=NAV_SESSION_ID,
                    model="test-model",
                    kind="subagent",
                    parent_session_id=WORKER_SESSION_ID,
                    ticket_id=TICKET_ID,
                )
            )
            db.add(
                AgentSession(
                    id=VERIFIER_SESSION_ID,
                    model="test-model",
                    kind="subagent",
                    parent_session_id=WORKER_SESSION_ID,
                    ticket_id=TICKET_ID,
                )
            )
            db.add(AgentSession(id=OTHER_SESSION_ID, model="test-model", kind="chat"))
            await db.flush()
            ticket.worker_session_id = WORKER_SESSION_ID
            await db.flush()
            db.add(
                AgentRun(
                    id=WORKER_RUN_ID,
                    root_run_id=WORKER_RUN_ID,
                    session_id=WORKER_SESSION_ID,
                    ticket_id=TICKET_ID,
                    agent_name="daily_ops",
                    kind="worker_turn",
                    status="running",
                    boot_id="boot-test",
                    started_at=BASE,
                )
            )
            db.add(
                AgentRun(
                    id=NAV_RUN_ID,
                    root_run_id=WORKER_RUN_ID,
                    parent_run_id=WORKER_RUN_ID,
                    session_id=NAV_SESSION_ID,
                    ticket_id=TICKET_ID,
                    agent_name="navigator",
                    kind="subagent",
                    status="completed",
                    boot_id="boot-test",
                    started_at=BASE,
                    finished_at=BASE + timedelta(minutes=5),
                )
            )
            await db.flush()
            for index, item in enumerate(audits):
                db.add(
                    AgentAudit(
                        caller="agent",
                        tool_name=item["tool_name"],
                        arguments={},
                        status=item.get("status", "success"),
                        risk_level=item.get("risk_level", "none"),
                        session_id=item.get("session_id", WORKER_SESSION_ID),
                        run_id=item.get("run_id", WORKER_RUN_ID),
                        ticket_id=TICKET_ID,
                        agent_name=item.get("agent_name", "daily_ops"),
                        created_at=item.get("created_at", BASE + timedelta(seconds=index)),
                    )
                )
                await db.flush()
            for index, item in enumerate(pipelines):
                status = item.get("status", "completed")
                default_finished = (
                    None
                    if status in {"pending", "running"}
                    else BASE + timedelta(minutes=index + 1)
                )
                db.add(
                    Pipeline(
                        id=item.get("id", f"pipeline-{index + 1}"),
                        source="agent",
                        priority=1,
                        task_count=1,
                        status=status,
                        title=item.get("title", f"日常 {index + 1}"),
                        agent_session_id=item.get(
                            "agent_session_id", WORKER_SESSION_ID
                        ),
                        retry_of_id=item.get("retry_of_id"),
                        error_message=item.get("error_message"),
                        created_at=item.get(
                            "created_at", BASE + timedelta(seconds=30 + index)
                        ),
                        finished_at=item.get("finished_at", default_finished),
                    )
                )
                await db.flush()
            if attempts:
                db.add(
                    ResourceAsset(
                        id="asset-copilot-1",
                        kind="copilot",
                        name="作业 1-7",
                        content={"actions": []},
                        meta={
                            "attempts": [
                                _attempt(index, dict(item))
                                for index, item in enumerate(attempts)
                            ]
                        },
                    )
                )
                await db.flush()
            for index, item in enumerate(verdicts):
                verdict = item.get("verdict") or {
                    "passed": item.get("passed", False),
                    "confidence": item.get("confidence", 0.0),
                    "evidence": list(item.get("evidence") or ()),
                    "notes": item.get("notes", ""),
                    "degraded": item.get("degraded", False),
                }
                db.add(
                    AgentRun(
                        id=item.get("run_id", f"run-verdict-{index + 1}"),
                        root_run_id=WORKER_RUN_ID,
                        parent_run_id=WORKER_RUN_ID,
                        session_id=VERIFIER_SESSION_ID,
                        ticket_id=TICKET_ID,
                        agent_name="verifier",
                        kind="subagent",
                        status=item.get("status", "completed"),
                        boot_id="boot-test",
                        started_at=BASE,
                        finished_at=item["finished_at"],
                        meta={"verdict": verdict},
                    )
                )
                await db.flush()
            for index, item in enumerate(questions):
                db.add(
                    AgentQuestion(
                        id=item.get("id", f"question-{index + 1}"),
                        session_id=item.get("session_id", WORKER_SESSION_ID),
                        tool_call_id=item.get("tool_call_id", f"call-{index + 1}"),
                        question=item.get("question", "还要继续试吗？"),
                        options=item.get("options", [WAIVER_OPTION, "再试一次"]),
                        status=item.get("status", "answered"),
                        selection=item.get("selection", [WAIVER_OPTION]),
                        answered_by="web",
                        created_at=BASE,
                        answered_at=item.get("answered_at", BASE + timedelta(hours=3)),
                    )
                )
                await db.flush()
            await db.commit()
        return VerificationGate(factory)

    gate = asyncio.run(build())
    return gate, TICKET_ID, WORKER_SESSION_ID


async def _check(
    gate: VerificationGate,
    ticket_id: str,
    worker_session_id: str,
    *,
    details: dict[str, Any] | None = None,
) -> Any:
    return await gate.check(
        ticket_id=ticket_id,
        worker_session_id=worker_session_id,
        claim=CLAIM,
        details=details,
    )


def _assert_accepted(decision: Any, method: str) -> dict[str, Any]:
    assert decision.accepted is True
    assert decision.method == method
    details = decision.report_details()
    assert set(details) <= DETAIL_KEYS
    assert details["method"] == method
    assert isinstance(details["evidence"], list)
    return details


def _assert_rejected(decision: Any, reason: str) -> dict[str, Any]:
    assert decision.accepted is False
    assert decision.reason == reason
    result = decision.tool_result()
    assert set(result) == REJECT_KEYS
    assert result["accepted"] is False
    assert result["reason"] == reason
    assert isinstance(result["message"], str) and result["message"]
    assert isinstance(result["evidence"], list)
    assert set(decision.report_details()) <= DETAIL_KEYS
    return result


# ---------------------------------------------------------------------------
# 只读工单与错绑会话：不过闸门
# ---------------------------------------------------------------------------
def test_no_worker_session_is_not_required(tmp_path: Path):
    empty_gate, ticket_id, _ = _world(tmp_path / "empty")
    parent_gate, parent_ticket_id, parent_session_id = _world(
        tmp_path / "parent", worker_session=False
    )

    async def scenario():
        first = await _check(empty_gate, ticket_id, "")
        assert first.accepted is True
        assert first.method == "not_required"
        second = await _check(empty_gate, ticket_id, "session-missing")
        assert second.accepted is True
        assert second.method == "not_required"
        parent = await _check(parent_gate, parent_ticket_id, parent_session_id)
        _assert_accepted(parent, "not_required")

    asyncio.run(scenario())


def test_read_only_audits_are_not_required(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path, audits=({"tool_name": "get_screenshot"},)
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_accepted(decision, "not_required")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 机器证据：流水线终态
# ---------------------------------------------------------------------------
def test_completed_pipelines_pass_by_machine(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "submit_pipeline"},),
        pipelines=(
            {"id": "pipeline-aaaa-1", "status": "completed", "title": "日常"},
            {"id": "pipeline-bbbb-2", "status": "completed", "title": "基建"},
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_accepted(decision, "machine")
        evidence = list(decision.evidence)
        assert len(evidence) == 2
        assert all(line.startswith("流水线 ") for line in evidence)

    asyncio.run(scenario())


def test_failed_pipeline_requires_verifier(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "submit_pipeline"},),
        pipelines=(
            {
                "id": "3f2a1c88-1111-2222-3333-444444444444",
                "status": "failed",
                "title": "日常",
                "error_message": "任务「刷取」失败",
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        result = _assert_rejected(decision, "verification_required")
        assert decision.delegate is not None
        assert decision.delegate["agent"] == "verifier"
        assert "3f2a1c88" in decision.delegate["context"]
        assert "日常" in result["evidence"][0]
        assert "failed" in result["evidence"][0]

    asyncio.run(scenario())


def test_running_pipeline_rejects_before_verifier(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "submit_pipeline"},),
        pipelines=(
            {
                "id": "aaaa1111-1111-2222-3333-444444444444",
                "status": "running",
                "title": "日常",
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        result = _assert_rejected(decision, "verification_required")
        assert decision.delegate is None
        assert "wait_pipeline" in result["message"]
        assert "aaaa1111" in result["message"]

    asyncio.run(scenario())


def test_raw_tools_block_machine_evidence(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=(
            {"tool_name": "submit_pipeline", "created_at": BASE},
            {"tool_name": "click", "created_at": BASE + timedelta(seconds=1)},
        ),
        pipelines=({"id": "pipeline-aaaa-1", "status": "completed"},),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_rejected(decision, "verification_required")
        assert decision.delegate is not None
        assert decision.delegate["agent"] == "verifier"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 机器证据：内核判定的作业结果
# ---------------------------------------------------------------------------
def test_core_judged_success_passes(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "run_copilot_attempt"},),
        attempts=({"success": True, "judged_by": "core"},),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_accepted(decision, "machine")
        assert decision.evidence[0].startswith("作业尝试 #1")
        assert "core" in decision.evidence[0]

    asyncio.run(scenario())


def test_model_judged_success_needs_verifier(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "run_copilot_attempt"},),
        attempts=({"success": True, "judged_by": "model"},),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_rejected(decision, "verification_required")
        assert decision.delegate is not None
        assert decision.delegate["agent"] == "verifier"
        assert "get_screenshot" in decision.delegate["context"]

    asyncio.run(scenario())


def test_attempt_pipelines_are_excluded(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=(
            {"tool_name": "submit_pipeline", "created_at": BASE},
            {"tool_name": "run_copilot_attempt", "created_at": BASE + timedelta(seconds=1)},
        ),
        pipelines=(
            {"id": "pipeline-attempt", "status": "failed", "title": "尝试失败"},
            {"id": "pipeline-extra", "status": "completed", "title": "补充流水线"},
        ),
        attempts=(
            {
                "success": True,
                "judged_by": "core",
                "pipeline_id": "pipeline-attempt",
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_accepted(decision, "machine")
        assert any("pipeline-extra"[:8] in line for line in decision.evidence)

    asyncio.run(scenario())


def test_superseded_retry_pipeline_is_excluded(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "submit_pipeline"},),
        pipelines=(
            {"id": "pipeline-old-1", "status": "failed", "title": "旧流水线"},
            {
                "id": "pipeline-new-2",
                "status": "completed",
                "title": "重试流水线",
                "retry_of_id": "pipeline-old-1",
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_accepted(decision, "machine")
        assert len(decision.evidence) == 1
        assert "pipeline-new-2"[:8] in decision.evidence[0]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 子代理点击不算工单效果
# ---------------------------------------------------------------------------
def test_subagent_device_writes_do_not_count(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=(
            {"tool_name": "get_screenshot", "created_at": BASE},
            {
                "tool_name": "click",
                "agent_name": "navigator",
                "run_id": NAV_RUN_ID,
                "created_at": BASE + timedelta(seconds=1),
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_accepted(decision, "not_required")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 验证员结论
# ---------------------------------------------------------------------------
def test_fresh_passing_verdict_passes(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=(
            {
                "run_id": "run-verdict-fresh",
                "finished_at": BASE + timedelta(hours=1),
                "passed": True,
                "confidence": 0.9,
                "evidence": ["截图显示 3 星"],
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        details = _assert_accepted(decision, "verifier")
        assert details["verdict_run_id"] == "run-verdict-fresh"
        assert details["evidence"] == ["截图显示 3 星"]

    asyncio.run(scenario())


def test_stale_verdict_is_ignored(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE + timedelta(hours=2)},),
        verdicts=(
            {
                "run_id": "run-verdict-stale",
                "finished_at": BASE + timedelta(hours=1),
                "passed": True,
                "confidence": 0.9,
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_rejected(decision, "verification_required")
        assert decision.delegate is not None

    asyncio.run(scenario())


def test_low_confidence_counts_as_failure(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=(
            {
                "run_id": "run-verdict-low",
                "finished_at": BASE + timedelta(hours=1),
                "passed": True,
                "confidence": VERIFY_MIN_CONFIDENCE - 0.1,
                "notes": "只看到一面",
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        result = _assert_rejected(decision, "verification_failed")
        assert "验证员认为还没完成" in result["message"]
        assert decision.delegate is not None

    asyncio.run(scenario())


def test_degraded_or_failed_run_verdict_counts(tmp_path: Path):
    degraded_gate, degraded_ticket, degraded_session = _world(
        tmp_path / "degraded",
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=(
            {
                "finished_at": BASE + timedelta(hours=1),
                "passed": True,
                "confidence": 0.9,
                "degraded": True,
            },
        ),
    )
    failed_gate, failed_ticket, failed_session = _world(
        tmp_path / "failed-run",
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=(
            {
                "finished_at": BASE + timedelta(hours=1),
                "status": "failed",
                "passed": False,
                "confidence": 0.2,
                "notes": "没看到通关标记",
            },
        ),
    )

    async def scenario():
        degraded = await _check(degraded_gate, degraded_ticket, degraded_session)
        _assert_rejected(degraded, "verification_failed")
        failed = await _check(failed_gate, failed_ticket, failed_session)
        _assert_rejected(failed, "verification_failed")

    asyncio.run(scenario())


def test_two_failures_exhaust(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=(
            {
                "finished_at": BASE + timedelta(hours=1),
                "passed": False,
                "notes": "第一次不通过",
            },
            {
                "finished_at": BASE + timedelta(hours=2),
                "passed": False,
                "notes": "第二次不通过",
            },
        ),
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        result = _assert_rejected(decision, "verification_exhausted")
        assert decision.delegate is None
        assert "两次验证不通过" in result["message"]
        assert VERIFY_MAX_ROUNDS == 2

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 用户放行
# ---------------------------------------------------------------------------
def _two_failures() -> tuple[dict[str, Any], ...]:
    return (
        {
            "run_id": "run-verdict-1",
            "finished_at": BASE + timedelta(hours=1),
            "passed": False,
            "notes": "第一次不通过",
        },
        {
            "run_id": "run-verdict-2",
            "finished_at": BASE + timedelta(hours=2),
            "passed": False,
            "notes": "第二次不通过",
        },
    )


def test_waiver_accepts_answered_question(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=_two_failures(),
        questions=(
            {
                "id": "question-waiver",
                "selection": [WAIVER_OPTION],
                "answered_at": BASE + timedelta(hours=3),
            },
        ),
    )

    async def scenario():
        decision = await _check(
            gate,
            ticket_id,
            worker_session_id,
            details={"verification_waiver": "question-waiver"},
        )
        details = _assert_accepted(decision, "waiver")
        assert details["question_id"] == "question-waiver"

    asyncio.run(scenario())


def test_waiver_requires_two_failures(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=(
            {
                "run_id": "run-verdict-1",
                "finished_at": BASE + timedelta(hours=1),
                "passed": False,
                "notes": "第一次不通过",
            },
        ),
        questions=(
            {
                "id": "question-waiver",
                "selection": [WAIVER_OPTION],
                "answered_at": BASE + timedelta(hours=3),
            },
        ),
    )

    async def scenario():
        decision = await _check(
            gate,
            ticket_id,
            worker_session_id,
            details={"verification_waiver": "question-waiver"},
        )
        result = _assert_rejected(decision, "waiver_invalid")
        assert "两次验证不通过" in result["message"]

    asyncio.run(scenario())


def test_waiver_rejects_wrong_session_option_or_time(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=_two_failures(),
        questions=(
            {
                "id": "question-other-session",
                "session_id": OTHER_SESSION_ID,
                "selection": [WAIVER_OPTION],
                "answered_at": BASE + timedelta(hours=3),
            },
            {
                "id": "question-wrong-option",
                "selection": ["再试一次"],
                "answered_at": BASE + timedelta(hours=3),
            },
            {
                "id": "question-too-early",
                "selection": [WAIVER_OPTION],
                "answered_at": BASE + timedelta(minutes=90),
            },
        ),
    )

    async def scenario():
        for question_id in (
            "question-other-session",
            "question-wrong-option",
            "question-too-early",
        ):
            decision = await _check(
                gate,
                ticket_id,
                worker_session_id,
                details={"verification_waiver": question_id},
            )
            _assert_rejected(decision, "waiver_invalid")

    asyncio.run(scenario())


def test_waiver_by_selection_after_restart(tmp_path: Path):
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        verdicts=_two_failures(),
        questions=(
            {
                "id": "question-restart",
                "selection": [WAIVER_OPTION],
                "answered_at": BASE + timedelta(hours=3),
            },
        ),
    )

    async def scenario():
        decision = await _check(
            gate,
            ticket_id,
            worker_session_id,
            details={"verification_waiver": {"selection": WAIVER_OPTION}},
        )
        details = _assert_accepted(decision, "waiver")
        assert details["question_id"] == "question-restart"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 委派上下文与证据条数上限
# ---------------------------------------------------------------------------
def test_context_is_capped(tmp_path: Path):
    pipelines = tuple(
        {
            "id": f"pipeline-{index:02d}-aaaa-bbbb-cccc-dddddddddddd",
            "status": "failed",
            "title": f"流水线 {index}",
        }
        for index in range(30)
    )
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        pipelines=pipelines,
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        _assert_rejected(decision, "verification_required")
        assert decision.delegate is not None
        context = decision.delegate["context"]
        assert len(context) <= MAX_CONTEXT_CHARS
        assert "另有 27 条未列出" in context
        assert MAX_CONTEXT_CHARS == 4000

    asyncio.run(scenario())


def test_evidence_is_capped(tmp_path: Path):
    pipelines = tuple(
        {
            "id": f"pipeline-{index:02d}-aaaa-bbbb-cccc-dddddddddddd",
            "status": "failed",
            "title": f"流水线 {index}",
        }
        for index in range(30)
    )
    gate, ticket_id, worker_session_id = _world(
        tmp_path,
        audits=({"tool_name": "click", "created_at": BASE},),
        pipelines=pipelines,
    )

    async def scenario():
        decision = await _check(gate, ticket_id, worker_session_id)
        result = _assert_rejected(decision, "verification_required")
        assert len(decision.evidence) <= MAX_EVIDENCE
        assert len(result["evidence"]) <= MAX_EVIDENCE
        assert MAX_EVIDENCE == 10

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 记录验证员结论
# ---------------------------------------------------------------------------
def _verdict_run(meta: dict[str, Any] | None = None) -> AgentRun:
    return AgentRun(
        id="run-verdict",
        root_run_id="run-verdict",
        session_id=VERIFIER_SESSION_ID,
        agent_name="verifier",
        kind="subagent",
        status="completed",
        boot_id="boot-test",
        started_at=BASE,
        finished_at=BASE + timedelta(hours=1),
        meta=meta,
    )


def _seed_verdict_run(tmp_path: Path, *, meta: dict[str, Any] | None):
    async def build():
        tmp_path.mkdir(parents=True, exist_ok=True)
        _engine, factory = await make_session_factory(tmp_path / "verdict.db")
        async with factory() as db:
            db.add(AgentSession(id=VERIFIER_SESSION_ID, model="test-model", kind="subagent"))
            await db.flush()
            db.add(_verdict_run(meta))
            await db.commit()
        return factory

    return asyncio.run(build())


def test_record_verdict_keeps_other_meta_keys(tmp_path: Path):
    factory = _seed_verdict_run(
        tmp_path, meta={"tool_groups": ["account"], "note": "保留我"}
    )
    gate = VerificationGate(factory)

    async def scenario():
        await gate.record_verdict(
            run_id="run-verdict",
            verdict={
                "passed": True,
                "confidence": 0.9,
                "evidence": ["截图显示 3 星"],
                "notes": "核对通过",
            },
            degraded=False,
        )
        async with factory() as db:
            stored = await db.get(AgentRun, "run-verdict")
            assert stored is not None
            meta = stored.meta or {}
            assert meta["tool_groups"] == ["account"]
            assert meta["note"] == "保留我"
            verdict = meta["verdict"]
            assert set(verdict) == {
                "passed",
                "confidence",
                "evidence",
                "notes",
                "degraded",
                "recorded_at",
            }
            assert verdict["passed"] is True
            assert verdict["confidence"] == 0.9
            assert verdict["evidence"] == ["截图显示 3 星"]
            assert verdict["notes"] == "核对通过"
            assert verdict["degraded"] is False
            assert isinstance(verdict["recorded_at"], str)
            assert verdict["recorded_at"].endswith("Z")

    asyncio.run(scenario())


def test_record_verdict_degraded(tmp_path: Path):
    factory = _seed_verdict_run(tmp_path, meta=None)
    gate = VerificationGate(factory)

    async def scenario():
        await gate.record_verdict(run_id="run-verdict", verdict=None, degraded=True)
        async with factory() as db:
            stored = await db.get(AgentRun, "run-verdict")
            assert stored is not None
            verdict = (stored.meta or {})["verdict"]
            assert verdict["passed"] is False
            assert verdict["confidence"] == 0
            assert verdict["degraded"] is True
            assert verdict["evidence"] == []
            assert verdict["notes"] == "验证员没有给出合法结论"
            assert verdict["recorded_at"].endswith("Z")

    asyncio.run(scenario())
