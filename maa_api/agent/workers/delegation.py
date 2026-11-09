"""子代理委派服务（M70 规格 §4.5.4）。

``DelegationService.delegate`` 是 ``delegate`` 工具的唯一实现：

- 校验深度（子代理不能再委派）与名单（父定义 ``delegates``）；
- 按 ``delegation.max_parallel`` 限流只读委派；导航员（``device="exclusive"``）在
  同一工单内按锁串行；
- 建子代理会话与运行，复用 G6 的 :func:`maa_api.agent.workers.pump.run_agent` 跑
  一轮，子运行与父运行共享 ``cancel_event``；
- 在 ``agent.delegate`` span 里跑完子运行，再按 ``spec.output_schema`` 校验最终
  正文；不合法时用 M67 的结构化调用重试一次，仍失败就返回降级结果。

子代理的消息只留在子会话里，父会话只看到工具结果；``subagent_start`` /
``subagent_end`` 事件经父运行的 ``emit`` 发出，SSE 流因此能把子运行串起来。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

from sqlalchemy import update

# 导入即注册 subagent_report / verifier_verdict 等 schema；结构化调用只按名字取。
from maa_api.agent.llm import schemas as _schemas  # noqa: F401
from maa_api.agent.harness.context import RunContext, RunOutcome
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.llm.cost import CostLedger
from maa_api.agent.llm.structured import complete_structured, get_schema
from maa_api.agent.trace.spans import current_span, reset_span, use_span
from maa_api.agent.workers.cards import effective_ceiling, task_card
from maa_api.agent.workers.pump import run_agent
from maa_api.agent.workers.verification import VerificationGate
from maa_api.db.models import AgentMessage, AgentSession, utcnow
from maa_api.db.repositories.agent import (
    AgentMessageRepository,
    AgentSessionRepository,
)
from maa_api.domain.enums import (
    AgentRunKind,
    AgentRunStatus,
    AgentSessionKind,
    AgentSessionStatus,
    ModelRole,
    SpanKind,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

__all__ = ["DelegationService"]

logger = logging.getLogger(__name__)

#: 子代理会话里的第一条用户消息；任务卡作为 ``role_card`` 注入运行。
SUBTASK_MESSAGE = "按任务卡完成任务。"
#: 子代理输出的长度上限（§4.5.4）：subagent_end 摘要、降级的 summary/notes。
END_SUMMARY_CHARS = 600
REPORT_SUMMARY_CHARS = 1500
VERDICT_NOTES_CHARS = 1000
#: 不合法 JSON 的重试提示里回传的错误文本长度。
CORRECTION_ERROR_CHARS = 300
#: 子代理会话标题的 task 截断长度与整条标题上限。
TITLE_TASK_CHARS = 40
TITLE_CHARS = 64
#: 父运行 state 里并行信号量的键。
SEMAPHORE_KEY = "delegation.semaphore"


def _strip_code_fence(text: str) -> str:
    """去掉首尾空白与一层 Markdown 代码围栏（规格 §4.5.4）。"""
    candidate = text.strip()
    if not candidate.startswith("```"):
        return candidate
    lines = candidate.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _degraded(schema_name: str, text: str) -> dict[str, Any]:
    """两次都没有给出合法结构时的降级结果（字段与规格逐字一致）。"""
    if schema_name == "verifier_verdict":
        return {
            "passed": False,
            "confidence": 0,
            "evidence": [],
            "notes": text[:VERDICT_NOTES_CHARS] or "验证员没有给出结论",
            "degraded": True,
        }
    if schema_name == "subagent_report":
        return {
            "summary": text[:REPORT_SUMMARY_CHARS] or "子代理没有给出结论",
            "findings": [],
            "uncertainties": [],
            "artifacts": [],
            "degraded": True,
        }
    # 内置子代理都定义了这两种 schema；未知 schema 仍给调用方一个可读的降级形状。
    return {"text": text[:REPORT_SUMMARY_CHARS] or "子代理没有给出结论", "degraded": True}


class DelegationService:
    """把一个独立子任务交给子代理，拿回结构化结论（规格 §4.5.4）。"""

    def __init__(
        self,
        *,
        app: Any,
        session_factory: Any,
        spec_registry: Any,
        run_service: Any,
        model_router: Any,
        span_recorder: Any,
        event_bus: RunEventBus | None,
    ) -> None:
        self._app = app
        self._session_factory = session_factory
        self._spec_registry = spec_registry
        self._run_service = run_service
        self._model_router = model_router
        #: 追踪关闭 / 尚未装配时为 ``None``；委派照常进行。
        self._span_recorder = span_recorder
        self._event_bus = event_bus
        #: 工单 id → 独占设备的串行锁（导航员）。跨轮保留，锁本身不占资源。
        self._exclusive_locks: dict[str | None, asyncio.Lock] = {}
        self._ledger: CostLedger | None = None

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    async def delegate(
        self, parent: RunContext, *, agent: str, task: str, context: str | None
    ) -> dict[str, Any]:
        """校验深度与名单后执行一次委派；返回子代理的结构化结论。"""
        if str(getattr(parent.spec, "kind", "")) == "subagent":
            raise AppError(
                ErrorCode.DELEGATION_DEPTH_EXCEEDED,
                "子代理不能再委派",
                {"agent": agent},
            )
        delegates = tuple(getattr(parent.spec, "delegates", ()) or ())
        if agent not in delegates:
            raise AppError(
                ErrorCode.DELEGATION_NOT_ALLOWED,
                f"{parent.spec.title}不能委派「{agent}」",
                {"agent": agent, "allowed": list(delegates)},
            )

        spec = self._spec_registry.get(agent)
        semaphore = parent.state.setdefault(
            SEMAPHORE_KEY,
            asyncio.Semaphore(
                max(1, int(get_settings().delegation.max_parallel))
            ),
        )
        lock = (
            self._exclusive_locks.setdefault(parent.ticket_id, asyncio.Lock())
            if str(getattr(spec, "device", "none")) == "exclusive"
            else nullcontext()
        )
        async with semaphore:
            async with lock:
                return await self._delegate_locked(
                    parent, spec, agent=agent, task=task, context=context
                )

    # ------------------------------------------------------------------
    # 一次委派的完整流程
    # ------------------------------------------------------------------
    async def _delegate_locked(
        self,
        parent: RunContext,
        spec: Any,
        *,
        agent: str,
        task: str,
        context: str | None,
    ) -> dict[str, Any]:
        session = await self._create_session(parent, spec, task)
        run: Any = None
        outcome: RunOutcome | None = None
        try:
            await self._append_user_message(session.id, SUBTASK_MESSAGE)
            run = await self._run_service.start(
                session_id=session.id,
                agent_name=agent,
                kind=AgentRunKind.SUBAGENT,
                spec_version=getattr(spec, "spec_version", None),
                parent_run_id=parent.run_id,
                ticket_id=parent.ticket_id,
            )
            await parent.emit(
                "subagent_start",
                {
                    "run_id": run.id,
                    "parent_run_id": parent.run_id,
                    "agent": agent,
                    "task": task,
                },
            )
            state = self._initial_state(parent, spec, task, context)
            handle, token = self._open_span(parent, run, agent)
            try:
                outcome = await run_agent(
                    self._app,
                    spec,
                    session,
                    run,
                    inbox_items=[],
                    cancel_event=parent.cancel_event,
                    cancel_reason=parent.cancel_reason,
                    state=state,
                    parent=parent,
                )
            except BaseException as exc:
                self._close_span(handle, token, status="error", error=exc)
                raise
            self._close_span(
                handle,
                token,
                status="ok" if str(outcome.status) == "succeeded" else "error",
            )

            result = await self.finalize_output(spec, session, run, outcome)
            await self._finish_run(run, outcome)
            if str(getattr(spec, "name", "")) == "verifier":
                # §4.5.4：验证员结论落进 agent_run.meta，失败只记 warning。
                try:
                    await VerificationGate(self._session_factory).record_verdict(
                        run_id=run.id,
                        verdict=result,
                        degraded=bool(result.get("degraded")),
                    )
                except Exception:  # noqa: BLE001 - 写结论不能影响委派结果
                    logger.warning(
                        "验证员结论没有落库：run=%s", run.id, exc_info=True
                    )
            await self._set_session_status(session.id, AgentSessionStatus.FINISHED)
            await parent.emit(
                "subagent_end",
                {
                    "run_id": run.id,
                    "status": outcome.status,
                    "summary": (outcome.final_text or "")[:END_SUMMARY_CHARS],
                },
            )
            stored = await self._safe_get_run(run.id)
            steps = int(getattr(stored, "steps", 0) or 0)
            return {
                **result,
                "agent": agent,
                "run_id": run.id,
                "steps_used": steps,
            }
        except asyncio.CancelledError:
            # 父运行的泵在宽限期后取消这一轮：子运行随之结束，收尾不再 await 太久。
            if run is not None:
                reason = str(parent.cancel_reason())
                status = (
                    AgentRunStatus.CANCELLED
                    if reason == "cancelled"
                    else AgentRunStatus.INTERRUPTED
                )
                try:
                    await asyncio.shield(
                        self._finish_run_quietly(
                            run, status=status, finish_reason=reason
                        )
                    )
                except asyncio.CancelledError:
                    pass
            raise
        except Exception as exc:
            if run is not None:
                await self._finish_run_quietly(
                    run,
                    status=AgentRunStatus.FAILED,
                    finish_reason="error",
                    error_code=_error_code(exc),
                    error_message=_error_message(exc),
                )
            raise
        finally:
            # 成功与中断都走这里：子代理会话不能留在 active。
            await self._set_session_status(
                session.id, AgentSessionStatus.FINISHED, quiet=True
            )

    async def _create_session(
        self, parent: RunContext, spec: Any, task: str
    ) -> AgentSession:
        """建一个 ``kind='subagent'`` 的会话，模型取 subagent 角色档案。"""
        profile = await self._model_router.resolve(ModelRole.SUBAGENT.value)
        session = AgentSession(
            title=f"{spec.title}：{task[:TITLE_TASK_CHARS]}"[:TITLE_CHARS],
            model=str(getattr(profile, "model", "") or "unconfigured")[:64],
            base_url=getattr(profile, "base_url", None),
            kind=AgentSessionKind.SUBAGENT,
            parent_session_id=parent.session_id,
            ticket_id=parent.ticket_id,
        )
        async with self._session_factory() as db:
            stored = await AgentSessionRepository(db).create(session)
            await db.commit()
        return stored

    @staticmethod
    def _initial_state(
        parent: RunContext, spec: Any, task: str, context: str | None
    ) -> dict[str, Any]:
        """子代理这一轮的起始 state：任务卡、风险上限与工单标题。"""
        ticket_title = parent.state.get("ticket.title") or ""
        return {
            "role_card": task_card(
                task,
                context,
                ticket_title=str(ticket_title),
                parent_title=str(parent.spec.title),
            ),
            "mandate.ceiling": effective_ceiling(
                str(getattr(spec, "risk_ceiling", "none")),
                parent.state.get("mandate.ceiling"),
            ),
            "ticket.title": ticket_title,
        }

    # ------------------------------------------------------------------
    # 输出校验与降级
    # ------------------------------------------------------------------
    async def finalize_output(
        self,
        spec: Any,
        session: AgentSession,
        run: Any,
        outcome: RunOutcome,
    ) -> dict[str, Any]:
        """按 ``output_schema`` 校验最终正文；不合法时结构化重试一次，再失败降级。"""
        schema_name = getattr(spec, "output_schema", None)
        text = (outcome.final_text or "").strip()
        if not schema_name:
            return {
                "text": text[:REPORT_SUMMARY_CHARS] or "子代理没有给出结论",
                "degraded": False,
            }
        try:
            data = self._validate_output(str(schema_name), text)
        except Exception as exc:  # noqa: BLE001 - 任何不合法都走重试与降级
            await self._append_user_message(
                session.id,
                "上一条回复不是合法的 JSON："
                f"{str(exc)[:CORRECTION_ERROR_CHARS]}。"
                "只输出符合格式的 JSON 对象。",
            )
            try:
                value = await self._structured_retry(spec, session, run, str(schema_name))
            except Exception:  # noqa: BLE001 - 重试仍失败就降级，不把错误抛给父运行
                logger.warning(
                    "子代理 %s 的输出两次都不合法，返回降级结果", spec.name, exc_info=True
                )
                return _degraded(str(schema_name), text)
            return {**value.model_dump(), "degraded": False}
        return {**data, "degraded": False}

    @staticmethod
    def _validate_output(schema_name: str, text: str) -> dict[str, Any]:
        """去围栏后 ``json.loads``，再按 schema 校验，返回字段字典。"""
        candidate = _strip_code_fence(text)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败：{exc}") from exc
        return get_schema(schema_name).model_validate(payload).model_dump()

    async def _structured_retry(
        self, spec: Any, session: AgentSession, run: Any, schema_name: str
    ) -> Any:
        """用 M67 的结构化调用再要一次；用量记在同一个运行上。"""
        result = await complete_structured(
            router=self._model_router,
            ledger=self._ledger_for(),
            role=str(getattr(spec, "model_role", None) or ModelRole.SUBAGENT.value),
            schema_name=schema_name,
            messages=await self._history_messages(spec, session.id),
            agent_name=str(spec.name),
            run_id=run.id,
            session_id=session.id,
        )
        return result.value

    async def _history_messages(
        self, spec: Any, session_id: str
    ) -> list[dict[str, Any]]:
        """子会话的历史（只取有正文的 user / assistant 消息），前面加系统提示。"""
        async with self._session_factory() as db:
            rows = await AgentMessageRepository(db).list_by_session(session_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": str(spec.body)}
        ]
        for row in rows:
            if str(row.role) not in ("user", "assistant"):
                continue
            content = row.content
            if not isinstance(content, str) or not content.strip():
                continue
            messages.append({"role": str(row.role), "content": content})
        return messages

    def _ledger_for(self) -> CostLedger:
        if self._ledger is None:
            self._ledger = CostLedger(self._session_factory)
        return self._ledger

    # ------------------------------------------------------------------
    # 追踪
    # ------------------------------------------------------------------
    def _open_span(
        self, parent: RunContext, run: Any, agent: str
    ) -> tuple[Any, Any]:
        """开 ``agent.delegate`` span；追踪不可用时返回 ``(None, None)``。"""
        recorder = self._span_recorder
        if recorder is None:
            return None, None
        try:
            handle = recorder.start_span(
                "agent.delegate",
                SpanKind.DELEGATE,
                trace_id=parent.root_run_id,
                run_id=run.id,
                parent=current_span(),
                attributes={"agent": agent, "run_id": run.id},
            )
            return handle, use_span(handle)
        except Exception:  # noqa: BLE001 - 追踪失败不能拖垮委派
            logger.warning("记录 agent.delegate span 失败", exc_info=True)
            return None, None

    @staticmethod
    def _close_span(
        handle: Any, token: Any, *, status: str, error: BaseException | None = None
    ) -> None:
        if token is not None:
            try:
                reset_span(token)
            except Exception:  # noqa: BLE001
                pass
        if handle is None:
            return
        attributes = (
            {"error.type": type(error).__name__} if error is not None else None
        )
        try:
            handle.end(status=status, attributes=attributes)
        except Exception:  # noqa: BLE001 - 同上
            logger.warning("结束 agent.delegate span 失败", exc_info=True)

    # ------------------------------------------------------------------
    # 落库
    # ------------------------------------------------------------------
    async def _append_user_message(self, session_id: str, content: str) -> None:
        now = utcnow()
        async with self._session_factory() as db:
            messages = AgentMessageRepository(db)
            seq = await messages.next_seq(session_id)
            await messages.create(
                AgentMessage(
                    session_id=session_id,
                    seq=seq,
                    role="user",
                    content=content,
                    created_at=now,
                )
            )
            await db.execute(
                update(AgentSession)
                .where(AgentSession.id == session_id)
                .values(
                    message_count=AgentSession.message_count + 1,
                    updated_at=now,
                    last_message_at=now,
                )
            )
            await db.commit()

    async def _finish_run(self, run: Any, outcome: RunOutcome) -> None:
        status = _run_status(outcome.status)
        await self._run_service.finish(
            run.id,
            status=status,
            finish_reason=str(outcome.finish_reason),
        )

    async def _finish_run_quietly(
        self,
        run: Any,
        *,
        status: AgentRunStatus,
        finish_reason: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        try:
            await self._run_service.finish(
                run.id,
                status=status,
                finish_reason=finish_reason,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception:  # noqa: BLE001 - 收尾失败不覆盖原始错误
            logger.warning("子代理运行 %s 收尾失败", getattr(run, "id", None))

    async def _safe_get_run(self, run_id: str) -> Any:
        try:
            return await self._run_service.get(run_id)
        except Exception:  # noqa: BLE001 - steps 只是附带信息
            logger.warning("读取子代理运行 %s 失败", run_id, exc_info=True)
            return SimpleNamespace(steps=0)

    async def _set_session_status(
        self, session_id: str, status: AgentSessionStatus, *, quiet: bool = False
    ) -> None:
        try:
            async with self._session_factory() as db:
                await db.execute(
                    update(AgentSession)
                    .where(AgentSession.id == session_id)
                    .values(status=status, updated_at=utcnow())
                )
                await db.commit()
        except Exception:  # noqa: BLE001
            if not quiet:
                raise
            logger.warning("子代理会话 %s 收尾失败", session_id, exc_info=True)


def _run_status(status: str) -> AgentRunStatus:
    try:
        return AgentRunStatus(str(status))
    except ValueError:
        return AgentRunStatus.FAILED


def _error_code(exc: Exception) -> str | None:
    return getattr(exc, "code", None) if isinstance(exc, AppError) else None


def _error_message(exc: Exception) -> str | None:
    return str(exc) if isinstance(exc, AppError) else None
