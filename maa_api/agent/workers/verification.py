"""完成闸门（M87 规格 §4.5.1–§4.5.4）。

``ready`` 汇报前由 ``WorkerService.submit_report`` 调 :meth:`VerificationGate.check`：
只统计**本工单 Worker 自己**的写设备审计（导航员等子代理的点击不算），按三类依据
放行——最后一次 ``submit_pipeline`` 之后本工单流水线全部 ``completed``、本会话最近
一次作业尝试 ``success=true`` 且 ``judged_by="core"``、最后一次写设备操作之后结束的
验证员结论。三类都不成立时返回 ``accepted=false`` 的拒绝结果（五个键，规格 §4.3），
``ReportMiddleware`` 见它就不结束运行、不写汇报。

验证员结论落在子运行的 ``agent_run.meta["verdict"]``（:meth:`record_verdict`）；
``ticket_report.details["verification"]`` 由 :meth:`GateDecision.report_details` 给出，
:func:`merge_verification` 负责并进 ``details`` 并按 4000 字裁剪 evidence（§4.5.3）。
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select

from maa_api.db.models import (
    AgentAudit,
    AgentQuestion,
    AgentRun,
    AgentSession,
    Pipeline,
    Ticket,
)
from maa_api.db.repositories.copilot import CopilotAttemptRepository

__all__ = [
    "EFFECT_TOOLS",
    "MAX_CONTEXT_CHARS",
    "MAX_DETAILS_CHARS",
    "MAX_EVIDENCE",
    "MAX_LISTED_ATTEMPTS",
    "MAX_LISTED_PIPELINES",
    "RAW_TOOLS",
    "VERIFICATION_META_CHARS",
    "VERIFY_MAX_ROUNDS",
    "VERIFY_MIN_CONFIDENCE",
    "WAIVER_OPTION",
    "EffectTool",
    "GateDecision",
    "GateMethod",
    "GateReason",
    "VerificationGate",
    "merge_verification",
]

_logger = logging.getLogger(__name__)

EffectTool = Literal[
    "submit_pipeline",
    "run_copilot_attempt",
    "click",
    "swipe",
    "long_press",
    "input_text",
    "key_event",
]
GateMethod = Literal["not_required", "machine", "verifier", "waiver"]
GateReason = Literal[
    "verification_required",
    "verification_failed",
    "verification_exhausted",
    "waiver_invalid",
]

#: 计入「工单效果」的写设备工具（§4.5.1）。
EFFECT_TOOLS: frozenset[str] = frozenset(
    {
        "submit_pipeline",
        "run_copilot_attempt",
        "click",
        "swipe",
        "long_press",
        "input_text",
        "key_event",
    }
)
#: 原子操作：用过它们就没有机器证据，只能靠验证员（§4.5.2 第 1 步）。
RAW_TOOLS: frozenset[str] = frozenset(
    {"click", "swipe", "long_press", "input_text", "key_event"}
)
#: 验证员结论算通过的最低把握（§4.5.2）。
VERIFY_MIN_CONFIDENCE = 0.7
#: 同一批操作之后最多两次不通过（§4.5.2 第 4 步）。
VERIFY_MAX_ROUNDS = 2
#: 用户放行的选项文案（§4.5.6）。
WAIVER_OPTION = "仍按完成结束"
#: 委派 context 总长上限（§4.5.2）。
MAX_CONTEXT_CHARS = 4000
#: 一次放行/拒绝的 evidence 至多几条（§4.5.2）。
MAX_EVIDENCE = 10
#: 委派 context 里最多列几条流水线 / 作业尝试（§4.5.2）。
MAX_LISTED_PIPELINES = 3
MAX_LISTED_ATTEMPTS = 3
#: ``ticket_report.details`` 的序列化上限（与 ``ReportParams`` 同口径，§4.5.3）。
MAX_DETAILS_CHARS = 4000
#: 闸门追加的可信元数据固定部分上限（§4.5.3）。
VERIFICATION_META_CHARS = 120
#: 降级结论的 notes（§4.5.4）。
_DEGRADED_NOTES = "验证员没有给出合法结论"

_REQUIRED_MESSAGE = (
    "这张工单做过写设备的操作，汇报完成前要先核对：按 delegate 委派验证员，"
    "通过后再汇报 ready。"
)
_RUNNING_MESSAGE = (
    "本工单还有流水线在运行（{prefix}「{title}」{status}）。"
    "先用 wait_pipeline 查到终态，再汇报 ready。"
)
_EXHAUSTED_MESSAGE = (
    "这批操作之后已经两次验证不通过，只能汇报 failed 或 blocked；"
    "用户明确接受现状时，用 ask_user 让用户选「仍按完成结束」，再带 verification_waiver 汇报。"
)
_WAIVER_INVALID_MESSAGE = (
    "用户放行只在同一批操作已有两次验证不通过之后可用；verification_waiver 还要对得上"
    "一张已回答、选了「仍按完成结束」、晚于最后一次写设备操作与最后一次验证的提问卡。"
)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """一次闸门判定。

    ``accepted=False`` 时 ``reason`` / ``message`` 是 ``report`` 的拒绝结果；
    ``accepted=True`` 时 ``method`` 写进汇报行的 ``details["verification"]``。
    """

    accepted: bool
    method: GateMethod | None = None
    reason: GateReason | None = None
    evidence: tuple[str, ...] = ()
    verdict_run_id: str | None = None
    question_id: str | None = None
    message: str | None = None
    delegate: Mapping[str, str] | None = None

    def report_details(self) -> dict[str, Any]:
        """写进 ``ticket_report.details["verification"]`` 的形状（§4.1）。"""
        if not self.accepted or self.method is None:
            return {}
        details: dict[str, Any] = {"method": self.method}
        if self.verdict_run_id is not None:
            details["verdict_run_id"] = self.verdict_run_id
        if self.question_id is not None:
            details["question_id"] = self.question_id
        details["evidence"] = list(self.evidence)
        return details

    def tool_result(self) -> dict[str, Any]:
        """``report`` 工具的返回（§4.3）：拒绝时恰好五个键。"""
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "message": self.message,
            "evidence": list(self.evidence),
            "delegate": dict(self.delegate) if self.delegate is not None else None,
        }


def _not_required() -> GateDecision:
    return GateDecision(accepted=True, method="not_required")


def _as_datetime(value: Any) -> datetime | None:
    """把 ``agent_run`` / ``pipeline`` 的 datetime 与尝试记录里的 ISO 串统一成 naive UTC。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _format_time(value: Any) -> str:
    moment = _as_datetime(value)
    if moment is None:
        return "未知"
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pipeline_line(pipeline: Pipeline) -> str:
    line = (
        f"流水线 {str(pipeline.id)[:8]}「{pipeline.title or ''}」{pipeline.status}"
    )
    if pipeline.finished_at is not None:
        line += f"，结束于 {_format_time(pipeline.finished_at)}"
    else:
        line += "，尚未结束"
    if pipeline.error_message:
        line += f"；失败原因：{pipeline.error_message[:80]}"
    return line


def _attempt_line(attempt: Mapping[str, Any] | None) -> str:
    if attempt is None:
        return "没有找到本工单的作业尝试记录"
    number = attempt.get("attempt_number")
    stage = attempt.get("stage_code") or attempt.get("stage_id") or "?"
    outcome = attempt.get("outcome") or "未知"
    judged_by = attempt.get("judged_by") or "未判定"
    counted = "计次" if attempt.get("counted") else "不计次"
    return (
        f"作业尝试 #{number if number is not None else '?'} 关卡 {stage}："
        f"{outcome}，判定方 {judged_by}，{counted}"
    )


def _verdict_of(run: AgentRun) -> Mapping[str, Any] | None:
    meta = run.meta
    if not isinstance(meta, Mapping):
        return None
    verdict = meta.get("verdict")
    return verdict if isinstance(verdict, Mapping) else None


def _verdict_passed(verdict: Mapping[str, Any]) -> bool:
    """``passed is True`` 且把握不低于 ``VERIFY_MIN_CONFIDENCE``；降级算不通过。"""
    if verdict.get("degraded"):
        return False
    if verdict.get("passed") is not True:
        return False
    confidence = verdict.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    return float(confidence) >= VERIFY_MIN_CONFIDENCE


def _verdict_evidence(verdict: Mapping[str, Any]) -> tuple[str, ...]:
    raw = verdict.get("evidence")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(str(item) for item in raw)[:MAX_EVIDENCE]


def _failed_message(verdict: Mapping[str, Any]) -> str:
    notes = str(verdict.get("notes") or "").strip()[:200]
    if not notes:
        notes = "验证员没有给出通过结论"
    return (
        f"验证员认为还没完成：{notes}。能补救就补救后再委派验证员；"
        "补救不了就汇报 failed 或 blocked。"
    )


def _selection_has_waiver(selection: Any) -> bool:
    return (
        isinstance(selection, Sequence)
        and not isinstance(selection, (str, bytes))
        and WAIVER_OPTION in selection
    )


def _suggest(
    ticket: Ticket,
    claim: str,
    used: set[str],
    pipelines: Sequence[Pipeline],
    attempts: Sequence[Mapping[str, Any]],
    raw_counts: Mapping[str, int],
) -> dict[str, str]:
    """生成验证员的 ``task`` / ``context``（§4.5.2 ``suggest()``）。"""
    task = f"核对工单「{ticket.title}」是否完成：{str(claim or '')[:300]}"
    lines = ["执行记录："]
    listed_pipelines = list(pipelines)[-MAX_LISTED_PIPELINES:]
    lines.extend(f"- {_pipeline_line(pipeline)}" for pipeline in listed_pipelines)
    hidden_pipelines = len(pipelines) - len(listed_pipelines)
    if hidden_pipelines > 0:
        lines.append(f"- 另有 {hidden_pipelines} 条未列出")
    listed_attempts = list(attempts)[-MAX_LISTED_ATTEMPTS:]
    lines.extend(f"- {_attempt_line(attempt)}" for attempt in listed_attempts)
    hidden_attempts = len(attempts) - len(listed_attempts)
    if hidden_attempts > 0:
        lines.append(f"- 另有 {hidden_attempts} 条未列出")
    lines.extend(
        f"- 原子操作 {name} × {count}"
        for name, count in sorted(raw_counts.items())
    )
    methods: list[str] = []
    if "submit_pipeline" in used:
        methods.append("get_pipeline 看各任务状态，失败的任务用 get_logs 看原因")
    if "run_copilot_attempt" in used:
        methods.append("get_screenshot 看关卡页上这一关的通关标记与星数")
    if raw_counts:
        methods.append("get_screenshot 看当前画面是否符合声称的结果")
    lines.append(
        "核对方法：" + ("；".join(methods) if methods else "get_screenshot 看当前画面")
    )
    context = "\n".join(lines)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS]
    if len(task) + len(context) > MAX_CONTEXT_CHARS:
        context = context[: max(0, MAX_CONTEXT_CHARS - len(task))]
    return {"agent": "verifier", "task": task, "context": context}


def _fallback_evidence(
    pipelines: Sequence[Pipeline],
    attempts: Sequence[Mapping[str, Any]],
    raw_counts: Mapping[str, int],
) -> list[str]:
    """没有机器证据时列出可核对的事实（§4.5.2 第 4 步的 ``evidence``）。"""
    lines = [_pipeline_line(pipeline) for pipeline in pipelines]
    lines.extend(_attempt_line(attempt) for attempt in attempts)
    lines.extend(
        f"原子操作 {name} × {count}" for name, count in sorted(raw_counts.items())
    )
    return lines[:MAX_EVIDENCE]


def _verdict_payload(verdict: Mapping[str, Any] | None, degraded: bool) -> dict[str, Any]:
    """``agent_run.meta["verdict"]`` 的形状（§4.5.4）。"""
    recorded_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if degraded or not isinstance(verdict, Mapping):
        return {
            "passed": False,
            "confidence": 0,
            "evidence": [],
            "notes": _DEGRADED_NOTES,
            "degraded": True,
            "recorded_at": recorded_at,
        }
    return {**dict(verdict), "degraded": False, "recorded_at": recorded_at}


def merge_verification(
    details: Mapping[str, Any] | None,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    """把 ``verification`` 并进 ``details``，超长时从 evidence 尾部逐条删（§4.5.3）。"""
    payload: dict[str, Any] = {
        **verification,
        "evidence": list(verification.get("evidence") or ()),
    }
    merged: dict[str, Any] = {**(details or {}), "verification": payload}
    if len(json.dumps(merged, ensure_ascii=False)) <= MAX_DETAILS_CHARS:
        return merged
    evidence = list(payload["evidence"])
    while evidence and len(json.dumps(merged, ensure_ascii=False)) > MAX_DETAILS_CHARS:
        evidence.pop()
        merged["verification"] = {**payload, "evidence": evidence}
    if len(json.dumps(merged, ensure_ascii=False)) > MAX_DETAILS_CHARS:
        merged["verification"] = {
            key: value for key, value in payload.items() if key != "evidence"
        }
    return merged


class VerificationGate:
    """``ready`` 的完成闸门；``session_factory`` 与其它服务同口径，现建现用。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def check(
        self,
        *,
        ticket_id: str,
        worker_session_id: str,
        claim: str,
        details: Mapping[str, Any] | None,
    ) -> GateDecision:
        """按 §4.5.2 判定一次 ``ready`` 是否可以放行。"""
        async with self._session_factory() as db:
            ticket = await db.get(Ticket, ticket_id) if ticket_id else None
            if ticket is None:
                return _not_required()
            if not worker_session_id or ticket.worker_session_id != worker_session_id:
                return _not_required()
            session = await db.get(AgentSession, worker_session_id)
            if (
                session is None
                or session.kind != "worker"
                or session.parent_session_id is not None
                or session.ticket_id != ticket.id
            ):
                return _not_required()

            effects = await self._effects(db, ticket, worker_session_id)
            if not effects:
                return _not_required()

            attempts = await CopilotAttemptRepository(db).list_for_session(
                worker_session_id
            )
            attempt_pipeline_ids = {
                str(value)
                for attempt in attempts
                for key in ("pipeline_id", "recognition_pipeline_id", "exit_pipeline_id")
                if (value := attempt.get(key))
            }
            submitted_pipelines = await self._pipelines(db, worker_session_id)
            superseded = {
                str(pipeline.retry_of_id)
                for pipeline in submitted_pipelines
                if pipeline.retry_of_id
            }
            pipelines = [
                pipeline
                for pipeline in submitted_pipelines
                if str(pipeline.id) not in attempt_pipeline_ids
                and str(pipeline.id) not in superseded
            ]
            moments = [audit.created_at for audit in effects]
            moments += [
                _as_datetime(attempt.get("finished_at"))
                for attempt in attempts
                if attempt.get("finished_at")
            ]
            moments += [
                pipeline.finished_at
                for pipeline in pipelines
                if pipeline.finished_at is not None
            ]
            last_effect_at = max(moment for moment in moments if moment is not None)

            used = {audit.tool_name for audit in effects}
            raw_counts = Counter(
                audit.tool_name for audit in effects if audit.tool_name in RAW_TOOLS
            )

            # 0) 有非终态的本工单流水线：先拒绝，不委派。
            running = [
                pipeline
                for pipeline in pipelines
                if pipeline.status in {"pending", "running"}
            ]
            if running:
                first = running[0]
                return GateDecision(
                    accepted=False,
                    reason="verification_required",
                    message=_RUNNING_MESSAGE.format(
                        prefix=str(first.id)[:8],
                        title=first.title or "",
                        status=first.status,
                    ),
                    evidence=tuple(_pipeline_line(item) for item in running)[
                        :MAX_EVIDENCE
                    ],
                    delegate=None,
                )

            # 1) 机器证据：只有没用过原子操作时才可能成立。
            machine_evidence: list[str] = []
            if not (used & RAW_TOOLS):
                ok = True
                if "run_copilot_attempt" in used:
                    latest_attempt = attempts[-1] if attempts else None
                    ok = (
                        latest_attempt is not None
                        and bool(latest_attempt.get("success"))
                        and latest_attempt.get("judged_by") == "core"
                    )
                    machine_evidence.append(_attempt_line(latest_attempt))
                if "submit_pipeline" in used:
                    last_submit = max(
                        audit.created_at
                        for audit in effects
                        if audit.tool_name == "submit_pipeline"
                    )
                    submitted = [
                        pipeline
                        for pipeline in pipelines
                        if pipeline.created_at >= last_submit
                    ]
                    ok = (
                        ok
                        and bool(submitted)
                        and all(pipeline.status == "completed" for pipeline in submitted)
                    )
                    machine_evidence.extend(
                        _pipeline_line(pipeline)
                        for pipeline in submitted[:MAX_LISTED_PIPELINES]
                    )
                if ok:
                    return GateDecision(
                        accepted=True,
                        method="machine",
                        evidence=tuple(machine_evidence)[:MAX_EVIDENCE],
                    )

            # 2) 验证员结论：只认 meta 里有 verdict、且晚于最后一次写设备操作的运行。
            verdict_runs = await self._verdict_runs(db, ticket, last_effect_at)
            latest = verdict_runs[-1] if verdict_runs else None
            if latest is not None and _verdict_passed(latest[1]):
                return GateDecision(
                    accepted=True,
                    method="verifier",
                    evidence=_verdict_evidence(latest[1]),
                    verdict_run_id=str(latest[0].id),
                )
            failed = [
                item for item in verdict_runs if not _verdict_passed(item[1])
            ]

            # 3) 用户放行：只有两次验证不通过之后才可用。
            waiver = (details or {}).get("verification_waiver")
            if waiver is not None:
                if len(failed) >= VERIFY_MAX_ROUNDS:
                    after = max(
                        last_effect_at,
                        _as_datetime(failed[-1][0].finished_at) or last_effect_at,
                    )
                    question_id = await self._waiver_question_id(
                        db, waiver, worker_session_id, after
                    )
                    if question_id is not None:
                        return GateDecision(
                            accepted=True,
                            method="waiver",
                            question_id=question_id,
                        )
                return GateDecision(
                    accepted=False,
                    reason="waiver_invalid",
                    message=_WAIVER_INVALID_MESSAGE,
                )

            # 4) 失败有界。
            if len(failed) >= VERIFY_MAX_ROUNDS:
                return GateDecision(
                    accepted=False,
                    reason="verification_exhausted",
                    message=_EXHAUSTED_MESSAGE,
                    evidence=_verdict_evidence(failed[-1][1]),
                )
            if failed:
                return GateDecision(
                    accepted=False,
                    reason="verification_failed",
                    message=_failed_message(failed[-1][1]),
                    evidence=_verdict_evidence(failed[-1][1]),
                    delegate=_suggest(
                        ticket, claim, used, pipelines, attempts, raw_counts
                    ),
                )
            evidence = (
                machine_evidence
                if machine_evidence
                else _fallback_evidence(pipelines, attempts, raw_counts)
            )
            return GateDecision(
                accepted=False,
                reason="verification_required",
                message=_REQUIRED_MESSAGE,
                evidence=tuple(evidence)[:MAX_EVIDENCE],
                delegate=_suggest(ticket, claim, used, pipelines, attempts, raw_counts),
            )

    async def _effects(
        self, db: Any, ticket: Ticket, worker_session_id: str
    ) -> list[AgentAudit]:
        """§4.5.1 第 3、4 步：本工单 Worker 自己且归属本会话根运行的写设备审计。"""
        result = await db.execute(
            select(AgentAudit)
            .where(
                AgentAudit.ticket_id == ticket.id,
                AgentAudit.session_id == worker_session_id,
                AgentAudit.agent_name == ticket.kind,
                AgentAudit.tool_name.in_(tuple(EFFECT_TOOLS)),
                AgentAudit.status == "success",
            )
            .order_by(AgentAudit.created_at, AgentAudit.id)
        )
        audits = list(result.scalars().all())
        if not audits:
            return []
        run_ids = {audit.run_id for audit in audits if audit.run_id}
        runs: dict[str, AgentRun] = {}
        if run_ids:
            runs_result = await db.execute(
                select(AgentRun).where(AgentRun.id.in_(run_ids))
            )
            runs = {str(run.id): run for run in runs_result.scalars().all()}
        effects: list[AgentAudit] = []
        for audit in audits:
            run = runs.get(str(audit.run_id)) if audit.run_id else None
            if run is None:
                continue
            if run.session_id != worker_session_id:
                continue
            if run.agent_name != ticket.kind:
                continue
            if run.parent_run_id is not None:
                continue
            effects.append(audit)
        return effects

    async def _pipelines(self, db: Any, worker_session_id: str) -> list[Pipeline]:
        result = await db.execute(
            select(Pipeline).where(Pipeline.agent_session_id == worker_session_id)
        )
        return sorted(
            result.scalars().all(), key=lambda pipeline: (pipeline.created_at, str(pipeline.id))
        )

    async def _verdict_runs(
        self, db: Any, ticket: Ticket, last_effect_at: datetime
    ) -> list[tuple[AgentRun, Mapping[str, Any]]]:
        """§4.5.2 第 2 步：本工单验证员运行里 ``meta`` 有结论、且晚于最后写操作的。"""
        result = await db.execute(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket.id, AgentRun.agent_name == "verifier"
            )
        )
        verdicts: list[tuple[AgentRun, Mapping[str, Any]]] = []
        for run in result.scalars().all():
            verdict = _verdict_of(run)
            if verdict is None:
                continue
            finished_at = _as_datetime(run.finished_at)
            if finished_at is None or finished_at <= last_effect_at:
                continue
            verdicts.append((run, verdict))
        verdicts.sort(key=lambda item: _as_datetime(item[0].finished_at) or datetime.min)
        return verdicts

    async def _waiver_question_id(
        self,
        db: Any,
        waiver: Any,
        worker_session_id: str,
        after: datetime,
    ) -> str | None:
        """校验 ``verification_waiver`` 并按需反查提问卡（§4.5.2 ``waiver_valid``）。"""
        if isinstance(waiver, str):
            if not waiver:
                return None
            question = await db.get(AgentQuestion, waiver)
            if question is None:
                return None
            if question.session_id != worker_session_id:
                return None
            if question.status != "answered":
                return None
            if not _selection_has_waiver(question.selection):
                return None
            answered_at = _as_datetime(question.answered_at)
            if answered_at is None or answered_at <= after:
                return None
            return str(question.id)
        if not isinstance(waiver, Mapping):
            return None
        if set(waiver) != {"selection"} or waiver.get("selection") != WAIVER_OPTION:
            return None
        result = await db.execute(
            select(AgentQuestion)
            .where(
                AgentQuestion.session_id == worker_session_id,
                AgentQuestion.status == "answered",
            )
            .order_by(
                AgentQuestion.answered_at.desc(), AgentQuestion.created_at.desc()
            )
        )
        for question in result.scalars().all():
            answered_at = _as_datetime(question.answered_at)
            if answered_at is None or answered_at <= after:
                continue
            if _selection_has_waiver(question.selection):
                return str(question.id)
        return None

    async def record_verdict(
        self,
        *,
        run_id: str,
        verdict: Mapping[str, Any] | None,
        degraded: bool,
    ) -> None:
        """把验证员结论写进 ``agent_run.meta["verdict"]``；失败只记 warning（§4.5.4）。"""
        try:
            async with self._session_factory() as db:
                run = await db.get(AgentRun, run_id)
                if run is None:
                    _logger.warning("验证员结论没有落库：运行 %s 不存在", run_id)
                    return
                meta = dict(run.meta) if isinstance(run.meta, Mapping) else {}
                meta["verdict"] = _verdict_payload(verdict, degraded)
                run.meta = meta
                db.add(run)
                await db.commit()
        except Exception:  # noqa: BLE001 - 写失败不能影响委派结果（§4.5.4）
            _logger.warning("验证员结论没有落库：运行 %s 写入失败", run_id, exc_info=True)
