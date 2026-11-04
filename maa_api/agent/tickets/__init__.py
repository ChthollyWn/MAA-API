"""工单领域的纯函数：委托、汇报与话题键（M69 规格 §4.2）。

``ThreadRouter`` / ``InboxService`` / ``TicketService`` 在 T5、T6 加入各自的模块，
本包只重导出当前已经落地的公开名。
"""

from maa_api.agent.tickets.mandates import (
    MAX_SCOPE_CHARS,
    ORIGIN_SOURCES,
    RISK_ORDER,
    build_mandate,
    ceiling_for,
    ceiling_risk,
    exceeds_ceiling,
    lower_ceiling,
    policy_version,
    strength_for,
)
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
from maa_api.agent.tickets.threads import (
    MAX_THREAD_KEY_CHARS,
    SOURCE_PREFIXES,
    THREAD_KEY_PATTERNS,
    RouteResult,
    ThreadKey,
    parse_thread_key,
)

__all__ = [
    "FORWARDED_STATUSES",
    "MAX_DETAILS_BYTES",
    "MAX_PROMPT_CHARS",
    "MAX_SCOPE_CHARS",
    "MAX_SUMMARY_CHARS",
    "MAX_THREAD_KEY_CHARS",
    "ORIGIN_SOURCES",
    "REPORT_TO_TICKET_STATUS",
    "RISK_ORDER",
    "SOURCE_PREFIXES",
    "THREAD_KEY_PATTERNS",
    "HumanAction",
    "ReportInput",
    "RouteResult",
    "ThreadKey",
    "build_mandate",
    "ceiling_for",
    "ceiling_risk",
    "exceeds_ceiling",
    "lower_ceiling",
    "parse_report",
    "parse_thread_key",
    "policy_version",
    "report_inbox_payload",
    "should_forward",
    "strength_for",
    "ticket_status_for",
]
