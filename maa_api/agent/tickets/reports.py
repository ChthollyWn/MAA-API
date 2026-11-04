"""Worker 汇报的输入模型、状态映射与转发规则（M69 规格 §4.2、v3 05 §4.1）。

``ReportInput`` 是「一次汇报」的契约：M70 的 ``report`` 工具直接复用它做参数
模型，``TicketService.record_report`` 收的也是它。本模块不碰数据库。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from maa_api.db.models import Ticket, TicketReport
from maa_api.domain.enums import ReportStatus, TicketOrigin, TicketStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "FORWARDED_STATUSES",
    "MAX_DETAILS_BYTES",
    "MAX_PROMPT_CHARS",
    "MAX_SUMMARY_CHARS",
    "REPORT_TO_TICKET_STATUS",
    "HumanAction",
    "ReportInput",
    "parse_report",
    "report_inbox_payload",
    "should_forward",
    "ticket_status_for",
]

MAX_SUMMARY_CHARS = 600
MAX_PROMPT_CHARS = 200
MAX_DETAILS_BYTES = 8192

REPORT_TO_TICKET_STATUS: Mapping[ReportStatus, TicketStatus] = {
    ReportStatus.PROGRESS: TicketStatus.OPEN,
    ReportStatus.WAITING: TicketStatus.WAITING,
    ReportStatus.READY: TicketStatus.DONE,
    ReportStatus.BLOCKED: TicketStatus.BLOCKED,
    ReportStatus.FAILED: TicketStatus.FAILED,
    ReportStatus.DROPPED: TicketStatus.CANCELLED,
}
#: 无条件进总控收件箱的汇报状态；dropped 只在工单来源为 user 时转发（05 §4.1）。
FORWARDED_STATUSES = frozenset(
    {
        ReportStatus.WAITING,
        ReportStatus.READY,
        ReportStatus.BLOCKED,
        ReportStatus.FAILED,
    }
)


class HumanAction(BaseModel):
    """需要用户做的一件事；``waiting`` / ``blocked`` 汇报的必填项。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: Literal["confirmation", "question", "decision"]
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    ref_id: str | None = Field(default=None, max_length=64)


class ReportInput(BaseModel):
    """一次汇报（05 §4.1）；M70 的 report 工具直接复用它做参数模型。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: ReportStatus
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    human_action: HumanAction | None = Field(default=None, validate_default=True)
    details: dict[str, Any] | None = None

    @field_validator("human_action")
    @classmethod
    def _require_human_action(
        cls, value: HumanAction | None, info: ValidationInfo
    ) -> HumanAction | None:
        """waiting / blocked 缺 human_action 时报在 ``human_action`` 字段上。"""
        if info.data.get("status") in (ReportStatus.WAITING, ReportStatus.BLOCKED):
            if value is None:
                raise ValueError(
                    "waiting 与 blocked 汇报必须带 human_action"
                )
        return value

    @model_validator(mode="after")
    def _check_human_action_and_details(self) -> ReportInput:
        if self.status in (ReportStatus.WAITING, ReportStatus.BLOCKED):
            if self.human_action is None:  # 防御：字段校验已覆盖
                raise ValueError(
                    "waiting 与 blocked 汇报必须带 human_action"
                )
        else:
            self.human_action = None
        if self.details is not None:
            try:
                encoded = json.dumps(self.details, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"details 不是可序列化为 JSON 的对象：{exc}") from exc
            if len(encoded) > MAX_DETAILS_BYTES:
                raise ValueError(
                    f"details 的 JSON 超过 {MAX_DETAILS_BYTES} 字节"
                )
        return self


def parse_report(data: Mapping[str, Any]) -> ReportInput:
    """把外部传来的汇报参数解析成 :class:`ReportInput`。

    Pydantic 的校验错误统一变成 ``INVALID_PARAMETER``；``details.issues`` 是
    逐条错误（``loc`` / ``msg`` / ``type``），保留字段路径供调用方提示。
    """
    try:
        return ReportInput.model_validate(dict(data))
    except ValidationError as exc:
        issues = [
            {
                "loc": [str(part) for part in error["loc"]],
                "msg": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "汇报内容不合法",
            {"issues": issues},
        ) from None


def ticket_status_for(status: ReportStatus) -> TicketStatus:
    """汇报状态 → 工单状态（05 §4.1 的映射表）。"""
    return REPORT_TO_TICKET_STATUS[ReportStatus(status)]


def should_forward(status: ReportStatus, origin: TicketOrigin | str) -> bool:
    """FORWARDED_STATUSES 转发；dropped 只在 origin=user 时转发；progress 不转发。"""
    status_value = ReportStatus(status)
    if status_value in FORWARDED_STATUSES:
        return True
    if status_value == ReportStatus.DROPPED:
        return origin == TicketOrigin.USER
    return False


def report_inbox_payload(ticket: Ticket, report: TicketReport) -> dict[str, Any]:
    """写进总控收件箱的汇报载荷（05 §2.4 的 ``【工单汇报】``）。"""
    return {
        "ticket_id": ticket.id,
        "ticket_title": ticket.title,
        "ticket_kind": ticket.kind,
        "report_id": report.id,
        "status": report.status,
        "summary": report.summary,
        "human_action": report.human_action,
    }
