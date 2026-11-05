"""后台工单推送：用户不在页面上时推一条 Web Push（M82 规格 §4.5.6、08 §1.5）。

本模块只做「一条汇报值不值得推、推什么、隔多久再推」的判断，投递交给注入的
``WebPushService.broadcast``；不涉及 v2 的确认、养成计划完成等既有通知，也不写
发件箱（M85 接手时替换投递实现）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from maa_api.domain.enums import ReportStatus

logger = logging.getLogger(__name__)

__all__ = ["LABELS", "TicketPushNotifier"]

#: 推送标题里的状态词（08 §1.5 的触达口径）。
LABELS: dict[ReportStatus, str] = {
    ReportStatus.READY: "已完成",
    ReportStatus.FAILED: "没做成",
    ReportStatus.BLOCKED: "卡住了",
    ReportStatus.WAITING: "等你决定",
}

#: 无可见页面时直接推送的汇报状态；waiting 另看 ``human_action.type``。
_PUSH_STATUSES = frozenset(
    {ReportStatus.READY, ReportStatus.FAILED, ReportStatus.BLOCKED}
)
#: waiting 里值得打扰用户的两类待办；confirmation 已有 v2 的推送。
_WAITING_ACTIONS = frozenset({"question", "decision"})
#: 08 §1.5：标题 40 字、正文 120 字。
_MAX_TITLE_CHARS = 40
_MAX_BODY_CHARS = 120


def _status(value: Any) -> ReportStatus | None:
    """把枚举或字符串归一成 :class:`ReportStatus`；不认识的状态不推送。"""
    try:
        return ReportStatus(value)
    except (TypeError, ValueError):
        return None


class TicketPushNotifier:
    """汇报监听器：没有可见页面时按工单限流推送（M82 §4.5.6）。"""

    def __init__(
        self,
        *,
        webpush: Any,
        has_visible_client: Callable[[], bool],
        clock: Callable[[], float] = time.monotonic,
        min_interval_seconds: float = 600.0,
    ) -> None:
        self._webpush = webpush
        self._has_visible_client = has_visible_client
        self._clock = clock
        self._min_interval_seconds = min_interval_seconds
        #: 每张工单上一次推送的时刻（``clock`` 的刻度）。
        self._last_push: dict[str, float] = {}
        #: 已推送过的 ``(ticket_id, human_action.ref_id)``，waiting 同 ref 只推一次。
        self._pushed_refs: set[tuple[str, str | None]] = set()

    async def on_report(self, ticket: Any, report: Any) -> None:
        """汇报监听器入口：按 §4.5.6 的条件决定是否推送。"""
        status = _status(getattr(report, "status", None))
        if status is None:
            return
        action = getattr(report, "human_action", None)
        action_type = getattr(action, "type", None)
        if status not in _PUSH_STATUSES and not (
            status is ReportStatus.WAITING and action_type in _WAITING_ACTIONS
        ):
            return
        if self._has_visible_client():
            return

        ref_id = getattr(action, "ref_id", None)
        if status is ReportStatus.WAITING and (ticket.id, ref_id) in self._pushed_refs:
            return
        now = self._clock()
        last = self._last_push.get(ticket.id)
        if last is not None and now - last < self._min_interval_seconds:
            return

        summary = getattr(report, "summary", "") or ""
        payload = {
            "title": f"{LABELS[status]}：{ticket.title}"[:_MAX_TITLE_CHARS],
            "body": str(summary)[:_MAX_BODY_CHARS],
            "tag": f"ticket:{ticket.id}",
            "data": {"url": f"/tickets/{ticket.id}", "ticketId": ticket.id},
        }
        try:
            await self._webpush.broadcast(payload)
        except Exception:  # noqa: BLE001 - 推送失败不能影响已落库的汇报
            logger.warning(
                "工单推送失败：ticket=%s status=%s",
                ticket.id,
                status.value,
                exc_info=True,
            )
        # 投递失败也记一次，避免不可达订阅让同一工单反复触发。
        self._last_push[ticket.id] = now
        if status is ReportStatus.WAITING:
            self._pushed_refs.add((ticket.id, ref_id))
