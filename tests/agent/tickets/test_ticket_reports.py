"""汇报的状态映射、转发规则与参数校验（M69 规格 §4.2、§6）。

边界情况照 §4.5.9：waiting 缺 ``human_action``、ready 带了 ``human_action``、
summary / prompt / details 超限。
"""

from __future__ import annotations

import json

import pytest

from maa_api.agent.tickets.reports import (
    FORWARDED_STATUSES,
    MAX_DETAILS_BYTES,
    MAX_PROMPT_CHARS,
    MAX_SUMMARY_CHARS,
    REPORT_TO_TICKET_STATUS,
    HumanAction,
    ReportInput,
    parse_report,
    report_inbox_payload,
    should_forward,
    ticket_status_for,
)
from maa_api.db.models import Ticket, TicketReport
from maa_api.domain.enums import ReportStatus, TicketOrigin, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode

_EXPECTED_STATUS = {
    ReportStatus.PROGRESS: TicketStatus.OPEN,
    ReportStatus.WAITING: TicketStatus.WAITING,
    ReportStatus.READY: TicketStatus.DONE,
    ReportStatus.BLOCKED: TicketStatus.BLOCKED,
    ReportStatus.FAILED: TicketStatus.FAILED,
    ReportStatus.DROPPED: TicketStatus.CANCELLED,
}
_EMPTY_DETAILS_BYTES = len(json.dumps({"blob": ""}).encode("utf-8"))


def _parse_error(data: dict) -> list[dict]:
    with pytest.raises(AppError) as exc_info:
        parse_report(data)
    error = exc_info.value
    assert error.code is ErrorCode.INVALID_PARAMETER
    assert error.message == "汇报内容不合法"
    issues = error.details["issues"]
    assert issues
    return issues


def test_status_mapping_table() -> None:
    assert REPORT_TO_TICKET_STATUS == _EXPECTED_STATUS
    assert set(REPORT_TO_TICKET_STATUS) == set(ReportStatus)
    for status, ticket_status in _EXPECTED_STATUS.items():
        assert REPORT_TO_TICKET_STATUS[status] is ticket_status
        assert ticket_status_for(status) is ticket_status
        assert ticket_status_for(status.value) is ticket_status


def test_forwarding_rules() -> None:
    assert FORWARDED_STATUSES == {
        ReportStatus.WAITING,
        ReportStatus.READY,
        ReportStatus.BLOCKED,
        ReportStatus.FAILED,
    }
    for status in ReportStatus:
        for origin in TicketOrigin:
            forward = status in FORWARDED_STATUSES or (
                status == ReportStatus.DROPPED and origin == TicketOrigin.USER
            )
            assert should_forward(status, origin) is forward
            assert should_forward(status.value, origin.value) is forward


def test_parse_report_validation() -> None:
    progress = parse_report({"status": "progress", "summary": " 正在刷 1-7 "})
    assert isinstance(progress, ReportInput)
    assert progress.status is ReportStatus.PROGRESS
    assert progress.summary == "正在刷 1-7"
    assert progress.human_action is None
    assert progress.details is None

    waiting = parse_report(
        {
            "status": "waiting",
            "summary": "需要确认",
            "human_action": {"type": "confirmation", "prompt": "可以吗"},
        }
    )
    assert isinstance(waiting.human_action, HumanAction)
    assert waiting.human_action.prompt == "可以吗"

    blocked = parse_report(
        {
            "status": "blocked",
            "summary": "卡住了",
            "human_action": {
                "type": "decision",
                "prompt": "要不要换方案",
                "ref_id": "q-1",
            },
        }
    )
    assert blocked.human_action is not None
    assert blocked.human_action.ref_id == "q-1"

    assert _parse_error({"status": "progress", "summary": ""})
    assert _parse_error({"status": "progress", "summary": "   "})
    assert _parse_error(
        {"status": "progress", "summary": "字" * (MAX_SUMMARY_CHARS + 1)}
    )
    assert _parse_error(
        {
            "status": "waiting",
            "summary": "需要确认",
            "human_action": {
                "type": "confirmation",
                "prompt": "可" * (MAX_PROMPT_CHARS + 1),
            },
        }
    )
    too_big = {"blob": "a" * (MAX_DETAILS_BYTES - _EMPTY_DETAILS_BYTES + 1)}
    assert len(json.dumps(too_big).encode("utf-8")) == MAX_DETAILS_BYTES + 1
    assert _parse_error(
        {"status": "progress", "summary": "跑着", "details": too_big}
    )
    assert _parse_error({"status": "not-a-status", "summary": "x"})
    assert _parse_error({"status": "progress", "summary": "x", "extra": 1})

    missing = _parse_error({"status": "waiting", "summary": "需要确认"})
    assert any(issue["loc"] == ["human_action"] for issue in missing)
    assert any("human_action" in issue["msg"] for issue in missing)
    blocked_missing = _parse_error({"status": "blocked", "summary": "卡住了"})
    assert any(issue["loc"] == ["human_action"] for issue in blocked_missing)

    ready = parse_report(
        {
            "status": "ready",
            "summary": "做完了",
            "human_action": {"type": "confirmation", "prompt": "请确认"},
        }
    )
    assert ready.human_action is None
    dropped = parse_report(
        {
            "status": "dropped",
            "summary": "不做了",
            "human_action": {"type": "question", "prompt": "还做吗"},
        }
    )
    assert dropped.human_action is None

    boundary = {"blob": "a" * (MAX_DETAILS_BYTES - _EMPTY_DETAILS_BYTES)}
    assert len(json.dumps(boundary).encode("utf-8")) == MAX_DETAILS_BYTES
    parsed = parse_report(
        {"status": "progress", "summary": "跑着", "details": boundary}
    )
    assert parsed.details == boundary


def test_report_inbox_payload_shape() -> None:
    ticket = Ticket(id="ticket-1", title="刷 1-7", kind="daily_ops", origin="user")
    report = TicketReport(
        id=7,
        ticket_id="ticket-1",
        status=ReportStatus.WAITING,
        summary="需要确认",
        human_action={"type": "confirmation", "prompt": "可以吗", "ref_id": None},
    )
    assert report_inbox_payload(ticket, report) == {
        "ticket_id": "ticket-1",
        "ticket_title": "刷 1-7",
        "ticket_kind": "daily_ops",
        "report_id": 7,
        "status": ReportStatus.WAITING,
        "summary": "需要确认",
        "human_action": {"type": "confirmation", "prompt": "可以吗", "ref_id": None},
    }
