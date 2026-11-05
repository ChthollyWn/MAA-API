"""后台工单推送（M82 规格 §4.5.6、§6）。

覆盖 §6 的三条：无可见页面时 ready/blocked/failed 各推一次且载荷正确；有可见
页面、progress、confirmation 类 waiting 不推；waiting 同 ``ref_id`` 只推一次，
同一工单两次推送之间至少隔 ``min_interval_seconds``。
"""

from __future__ import annotations

import asyncio
from typing import Any

from maa_api.agent.tickets.push import LABELS, TicketPushNotifier
from maa_api.agent.tickets.reports import parse_report
from maa_api.db.models import Ticket
from maa_api.domain.enums import ReportStatus


class _WebPush:
    """记录推载荷的发件箱替身；``enqueue`` 是 M85 的生产路径。"""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    async def enqueue(self, payload: dict[str, Any], **kwargs: Any) -> None:
        self.payloads.append(payload)
        self.calls.append({"payload": payload, **kwargs})

    async def broadcast(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


class _Clock:
    """可调的单调时钟。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _ticket(ticket_id: str = "t-1", title: str = "日常") -> Ticket:
    return Ticket(id=ticket_id, title=title, kind="daily_ops", origin="user")


def _report(status: str, summary: str = "做完了"):
    return parse_report({"status": status, "summary": summary})


def _waiting(ref_id: str | None) -> Any:
    return parse_report(
        {
            "status": "waiting",
            "summary": "选一个方案",
            "human_action": {
                "type": "decision",
                "prompt": "用哪个方案",
                "ref_id": ref_id,
            },
        }
    )


def _notifier(
    webpush: _WebPush | None = None,
    *,
    visible: bool = False,
    clock: _Clock | None = None,
) -> tuple[TicketPushNotifier, _WebPush]:
    sink = webpush or _WebPush()
    return (
        TicketPushNotifier(
            outbox=sink,
            has_visible_client=lambda: visible,
            clock=clock or _Clock(),
        ),
        sink,
    )


def test_pushes_ready_blocked_failed_when_no_visible_client() -> None:
    async def scenario() -> None:
        notifier, webpush = _notifier()
        reports = [
            (ReportStatus.READY, _report("ready", "刷完了")),
            (
                ReportStatus.BLOCKED,
                parse_report(
                    {
                        "status": "blocked",
                        "summary": "设备连不上",
                        "human_action": {
                            "type": "decision",
                            "prompt": "要不要重启模拟器",
                        },
                    }
                ),
            ),
            (ReportStatus.FAILED, _report("failed", "关卡不存在")),
        ]
        for index, (status, report) in enumerate(reports):
            ticket = _ticket(f"t-{index}", f"工单 {index}")
            await notifier.on_report(ticket, report)

        assert len(webpush.payloads) == 3
        for index, (status, report) in enumerate(reports):
            payload = webpush.payloads[index]
            assert payload == {
                "title": f"{LABELS[status]}：工单 {index}",
                "body": report.summary,
                "tag": f"ticket:t-{index}",
                "data": {"url": f"/tickets/t-{index}", "ticketId": f"t-{index}"},
            }
            call = webpush.calls[index]
            assert call["kind"].value == "report"
            assert call["dedupe_key"].startswith(f"ticket:t-{index}:")
            assert call["ref_ticket_id"] == f"t-{index}"

    asyncio.run(scenario())


def test_no_push_when_client_visible_or_progress_or_confirmation_waiting() -> None:
    async def scenario() -> None:
        visible_notifier, visible_sink = _notifier(visible=True)
        await visible_notifier.on_report(_ticket(), _report("ready"))
        assert visible_sink.payloads == []

        notifier, webpush = _notifier()
        confirmation = parse_report(
            {
                "status": "waiting",
                "summary": "请确认",
                "human_action": {"type": "confirmation", "prompt": "可以吗"},
            }
        )
        await notifier.on_report(_ticket("t-progress"), _report("progress"))
        await notifier.on_report(_ticket("t-confirm"), confirmation)
        await notifier.on_report(_ticket("t-dropped"), _report("dropped"))
        assert webpush.payloads == []

    asyncio.run(scenario())


def test_waiting_pushed_once_per_ref_and_interval_limit() -> None:
    async def scenario() -> None:
        clock = _Clock(1000.0)
        notifier, webpush = _notifier(clock=clock)
        ticket = _ticket("t-waiting")

        await notifier.on_report(ticket, _waiting("q-1"))
        assert len(webpush.payloads) == 1
        assert webpush.payloads[0]["title"] == "等你决定：日常"

        # 新 ref_id 但距上次推送不到 10 分钟：限流，不记录 ref。
        clock.now = 1500.0
        await notifier.on_report(ticket, _waiting("q-2"))
        assert len(webpush.payloads) == 1

        # 过了 10 分钟：同一个新 ref_id 可以推。
        clock.now = 1700.0
        await notifier.on_report(ticket, _waiting("q-2"))
        assert len(webpush.payloads) == 2

        # 同一个 ref_id 只推一次：即使已过 10 分钟也不再推。
        clock.now = 2400.0
        await notifier.on_report(ticket, _waiting("q-1"))
        assert len(webpush.payloads) == 2

    asyncio.run(scenario())
