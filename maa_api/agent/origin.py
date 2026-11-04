"""工具调用的代理来源：wire 形状与「等人」时的工单冒泡（M71 规格 §4.2、§4.5.6）。

三块职责刻意放在一起，因为它们共享同一个 :class:`~maa_api.agent.registry.ToolOrigin`
类型与同一组 ``app.state`` 依赖：

- :func:`ticket_titles` / :func:`origin_wire`：把 ``run_id`` / ``ticket_id`` /
  ``agent`` 三列组装成协议里的 ``origin`` 对象（docs/v3/04 §4.2）。``run_id`` 为空即
  「不是代理发起」，整体返回 ``None``，协议里就是 ``null``。
- :func:`bubble_human_request`：代理开始等用户时让出隐式租约，把工单改成
  ``waiting``，并通知总控。
- :func:`settle_human_request`：确认或提问进入终态后把工单改回 ``open``。

两个冒泡函数都只吞异常记日志：确认与提问本身已经落库，冒泡是附带动作，失败不能
把它们变成错误。T7 才把两个函数接进 ``PolicyMiddleware`` / ``ClarificationMiddleware``。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.registry import ToolOrigin
from maa_api.agent.tickets.reports import HumanAction
from maa_api.db.repositories.ticket import TicketRepository
from maa_api.domain.enums import InboxKind

logger = logging.getLogger(__name__)


async def ticket_titles(
    session: AsyncSession, ticket_ids: Iterable[str | None]
) -> dict[str, str]:
    """去掉空值后调用 M69 的 ``TicketRepository(session).titles(...)``。"""
    ids = list(dict.fromkeys(ticket_id for ticket_id in ticket_ids if ticket_id))
    if not ids:
        return {}
    return await TicketRepository(session).titles(ids)


def origin_wire(
    *,
    run_id: str | None,
    ticket_id: str | None,
    agent: str | None,
    titles: Mapping[str, str],
) -> dict[str, Any] | None:
    """``run_id`` 为空返回 ``None``，否则 ``{ticket_id, ticket_title, agent, run_id}``。

    协议（docs/v3/04 §4.2）里 origin 只有一种对象形状；没有工单的代理运行也带
    ``ticket_id`` / ``ticket_title`` 两个键（值为 ``null``），前端按字段存在与否
    取不到任何分支差异。
    """
    if not run_id:
        return None
    return {
        "ticket_id": ticket_id,
        "ticket_title": titles.get(ticket_id) if ticket_id else None,
        "agent": agent,
        "run_id": run_id,
    }


async def bubble_human_request(
    state: Any,
    origin: ToolOrigin | None,
    *,
    root_run_id: str,
    kind: Literal["confirmation", "question"],
    ref_id: str,
    prompt: str,
) -> None:
    """确认或提问建好之后调用（§4.5.6）；任何异常只记日志，不影响确认或提问本身。"""
    try:
        lease_service = getattr(state, "device_lease_service", None)
        if lease_service is not None:
            # 等人时让出隐式租约：等待可能很久，不能让整台设备跟着空转（09 §3.2）。
            await lease_service.release_run(root_run_id, reason="waiting_human")
        if origin is None or not origin.ticket_id:
            return
        ticket_service = getattr(state, "ticket_service", None)
        thread_router = getattr(state, "thread_router", None)
        if ticket_service is None or thread_router is None:
            return
        ticket = await ticket_service.await_human(
            origin.ticket_id,
            HumanAction(type=kind, prompt=prompt[:200], ref_id=ref_id),
        )
        if ticket is None:
            return
        await thread_router.to_orchestrator(
            kind=InboxKind.SYSTEM,
            thread_key=f"ticket:{ticket.id}",
            ref_id=ref_id,
            payload={
                "event": "human_request",
                "type": kind,
                "ticket_id": ticket.id,
                "ticket_title": ticket.title,
                "agent": origin.agent,
                "ref_id": ref_id,
                "prompt": prompt[:200],
            },
            preferred_session_id=ticket.orchestrator_session_id,
        )
    except Exception:  # noqa: BLE001 - 冒泡失败不能影响已经落库的确认或提问
        logger.exception("上报人工待办失败：%s", ref_id)


async def settle_human_request(
    state: Any, origin: ToolOrigin | None, *, ref_id: str
) -> None:
    """确认或提问进入终态后调用：只有 ``ref_id`` 相同才把工单改回 ``open``。"""
    try:
        if origin is None or not origin.ticket_id:
            return
        ticket_service = getattr(state, "ticket_service", None)
        if ticket_service is None:
            return
        await ticket_service.human_resolved(origin.ticket_id, ref_id)
    except Exception:  # noqa: BLE001 - 收尾失败只记日志，确认或提问本身已经完成
        logger.exception("收尾人工待办失败：%s", ref_id)
