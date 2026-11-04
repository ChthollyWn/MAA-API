"""工单标题生成（M70 规格 §4.7.7）。

``TicketTitler.title_for`` 走 M67 的结构化调用：``title`` schema、角色 ``title``、
系统提示是 ``titler.md`` 正文。标题只是锦上添花，任何失败都退回文本本身，**不向
调用方抛异常**；每次尝试都留一条 ``agent_run``（``kind='title'``，``session_id=NULL``），
成本账本有 ``session_factory`` 时照常记。
"""

from __future__ import annotations

import logging
from typing import Any

# 导入即注册 ``title`` / ``subagent_report`` / ``verifier_verdict`` 三个 schema；
# ``complete_structured`` 只按名字取，不会替调用方导入。
from maa_api.agent.llm import schemas as _schemas  # noqa: F401
from maa_api.agent.llm.cost import CostLedger
from maa_api.agent.llm.structured import complete_structured
from maa_api.domain.enums import AgentRunKind, AgentRunStatus, ModelRole

__all__ = ["TicketTitler"]

logger = logging.getLogger(__name__)

#: 模型看到的任务说明上限（§4.7.7）。
MAX_INPUT_CHARS = 300
#: 标题上限；schema 已经限制 16 字，这里再兜一次。
MAX_TITLE_CHARS = 16
#: 全空任务说明的兜底标题。
EMPTY_TITLE = "新工单"
#: ``titler`` 代理定义的名字。
TITLER_NAME = "titler"


class _NullLedger:
    """没有账本可用时的空实现：标题调用不记成本，预算查询恒为 0。"""

    async def record(self, **_kwargs: Any) -> int:
        return 0

    async def run_spent_micros(self, _run_id: str) -> int:
        return 0

    async def spent_today_micros(self, *, now: Any = None) -> int:
        return 0

    def warn(self, **_kwargs: Any) -> None:
        return None


class TicketTitler:
    """根据任务说明生成不超过 16 字的工单标题。"""

    def __init__(
        self, *, spec_registry: Any, run_service: Any, model_router: Any
    ) -> None:
        self._spec_registry = spec_registry
        self._run_service = run_service
        self._model_router = model_router
        self._ledger: Any = None

    async def title_for(self, text: str) -> str:
        """返回标题；失败退回 ``text`` 的第一行前 16 字，为空用「新工单」。"""
        raw = "" if text is None else str(text)
        if not raw.strip():
            return EMPTY_TITLE
        run: Any = None
        try:
            spec = self._spec_registry.get(TITLER_NAME)
            run = await self._run_service.start(
                agent_name=TITLER_NAME,
                kind=AgentRunKind.TITLE,
                session_id=None,
                spec_version=getattr(spec, "spec_version", None),
            )
            result = await complete_structured(
                router=self._model_router,
                ledger=self._ledger_for(),
                role=str(getattr(spec, "model_role", None) or ModelRole.TITLE),
                schema_name="title",
                messages=[
                    {"role": "system", "content": spec.body},
                    {"role": "user", "content": raw[:MAX_INPUT_CHARS]},
                ],
                agent_name=TITLER_NAME,
                run_id=getattr(run, "id", None),
                session_id=None,
            )
            title = str(getattr(result.value, "title", "")).strip()
            if not title:
                raise ValueError("模型给出了空标题")
            await self._run_service.finish(
                run.id, status=AgentRunStatus.SUCCEEDED, finish_reason="stop"
            )
            return title[:MAX_TITLE_CHARS]
        except Exception:  # noqa: BLE001 - 标题失败不能拖累建单
            logger.warning("工单标题生成失败，改用文本兜底", exc_info=True)
            await self._finish_failed(run)
            return self._fallback(raw)

    def _ledger_for(self) -> Any:
        """优先用运行服务的库会话建账本；没有就退回空账本（标题不记成本）。"""
        if self._ledger is not None:
            return self._ledger
        factory = getattr(self._run_service, "session_factory", None)
        self._ledger = CostLedger(factory) if factory is not None else _NullLedger()
        return self._ledger

    async def _finish_failed(self, run: Any) -> None:
        if run is None:
            return
        try:
            await self._run_service.finish(
                run.id, status=AgentRunStatus.FAILED, finish_reason="error"
            )
        except Exception:  # noqa: BLE001 - 收尾失败不覆盖兜底标题
            logger.warning("标题运行 %s 收尾失败", getattr(run, "id", None))

    @staticmethod
    def _fallback(text: str) -> str:
        stripped = text.strip()
        if not stripped:
            return EMPTY_TITLE
        return stripped.splitlines()[0][:MAX_TITLE_CHARS] or EMPTY_TITLE
