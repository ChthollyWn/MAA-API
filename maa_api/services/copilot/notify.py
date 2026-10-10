"""``copilot_research_finished`` 的事件与载荷构造（M29，规格 §9.9）。

作业研究以授权进入终态为结束信号：那一刻发一条通知，经 ``NotifyService.send_event``
到订阅了该事件的渠道，同时经 ``WebPushService.broadcast`` 推到浏览器（规格 §9.9 把
Web Push「只用于待确认提醒」的口径放宽到这条事件，取舍记在 docs/13 §8）。

载荷一份两用：``message`` 是通知渠道的正文（``notify_service._message`` 读它），
``body`` 是同一句话的浏览器推送形态，两条路径共用，避免两边文案漂移。标题与正文
只写人话——关卡、结果、次数、最终作业标题——``copilot_id`` 与 ``session_id`` 这类
内部标识符只进结构化字段，不进标题正文（与 ``build_pipeline_notification``、
``confirmation_title`` 同一条原则）。

结果口径是七个值（:data:`RESULT_LABELS`）。调用方手上可能是授权状态
（``succeeded`` / ``exhausted`` / ``stopped`` / ``expired`` / ``revoked`` /
``interrupted``），也可能是尝试结果（``sanity_insufficient`` / ``error``），
:func:`_normalize_result` 负责落到这七个值上；认不出的值按「出错」发送并记一条
warning——通知是这条链路的终点，不能因为一个字符串就发不出去。
"""

from __future__ import annotations

import logging
from typing import Any

from maa_api.domain.enums import NotifyEvent

__all__ = [
    "RESULT_ERROR",
    "RESULT_EXHAUSTED",
    "RESULT_EXPIRED",
    "RESULT_INTERRUPTED",
    "RESULT_LABELS",
    "RESULT_REVOKED",
    "RESULT_SANITY_INSUFFICIENT",
    "RESULT_SUCCEEDED",
    "build_copilot_research_notification",
]

logger = logging.getLogger(__name__)

#: 规格 §9.9 的七种结果。取值与 ``copilot_grant.status`` 对齐，少一层翻译。
RESULT_SUCCEEDED = "succeeded"
RESULT_EXHAUSTED = "exhausted"
RESULT_SANITY_INSUFFICIENT = "sanity_insufficient"
RESULT_ERROR = "error"
RESULT_REVOKED = "revoked"
RESULT_EXPIRED = "expired"
RESULT_INTERRUPTED = "interrupted"

#: 结果 → 中文短句（通知标题与正文用）。
RESULT_LABELS: dict[str, str] = {
    RESULT_SUCCEEDED: "成功",
    RESULT_EXHAUSTED: "次数用完",
    RESULT_SANITY_INSUFFICIENT: "理智不足",
    RESULT_ERROR: "出错",
    RESULT_REVOKED: "已撤销",
    RESULT_EXPIRED: "已过期",
    RESULT_INTERRUPTED: "被中断",
}

#: 每种结果的一句话后续说明（正文最后一行）：用户看完通知知道下一步做什么。
RESULT_HINTS: dict[str, str] = {
    RESULT_SUCCEEDED: "已达成成功标准，这一关的研究可以结束了。",
    RESULT_EXHAUSTED: "授权次数已用完，本次研究结束；作业页里能看到每一版的结果。",
    RESULT_SANITY_INSUFFICIENT: "游戏里理智不足，本次研究已停下；补充理智后可以重新申请授权。",
    RESULT_ERROR: "执行过程中出错，本次研究已停下；失败原因见日志页。",
    RESULT_REVOKED: "授权已被撤销，本次研究结束。",
    RESULT_EXPIRED: "授权窗口已过期，剩余次数作废，本次研究结束。",
    RESULT_INTERRUPTED: "服务重启中断了本次研究；重新申请授权后可以接着试。",
}

#: 调用方可能传来的别名 → 七个结果值。``stopped`` 按主要成因（理智不足）处理：
#: 用户中断本轮时调用方应直接传 ``interrupted``（规格 §9.7 的 outcome 表）。
_RESULT_ALIASES: dict[str, str] = {
    "success": RESULT_SUCCEEDED,
    "succeeded": RESULT_SUCCEEDED,
    "three_star": RESULT_SUCCEEDED,
    "exhausted": RESULT_EXHAUSTED,
    "used_up": RESULT_EXHAUSTED,
    "attempts_exhausted": RESULT_EXHAUSTED,
    "stopped": RESULT_SANITY_INSUFFICIENT,
    "sanity": RESULT_SANITY_INSUFFICIENT,
    "no_sanity": RESULT_SANITY_INSUFFICIENT,
    "sanity_insufficient": RESULT_SANITY_INSUFFICIENT,
    "failed": RESULT_ERROR,
    "failure": RESULT_ERROR,
    "error": RESULT_ERROR,
    "unknown": RESULT_ERROR,
    "revoked": RESULT_REVOKED,
    "cancelled": RESULT_REVOKED,
    "canceled": RESULT_REVOKED,
    "expired": RESULT_EXPIRED,
    "timeout": RESULT_EXPIRED,
    "interrupted": RESULT_INTERRUPTED,
    "aborted": RESULT_INTERRUPTED,
}


def build_copilot_research_notification(
    result: str,
    *,
    stage_code: str = "",
    stage_name: str = "",
    stage_id: str = "",
    attempts_used: int = 0,
    attempts_total: int = 0,
    copilot_id: str = "",
    copilot_title: str = "",
    session_id: str = "",
) -> tuple[NotifyEvent, dict[str, Any]]:
    """构造 ``copilot_research_finished`` 的事件与载荷（规格 §9.9）。

    :param result: 结束原因；七个结果值之一，也接受授权状态与尝试结果的别名。
    :param stage_code: 关卡代号（``1-7``），展示在标题里。
    :param stage_name: 关卡名（``苦难摇篮``），与代号一起组成「1-7 苦难摇篮」。
    :param stage_id: 关卡内部 id，只进载荷（前端深链用）。
    :param attempts_used: 已用次数，负数按 0 处理。
    :param attempts_total: 总次数，0 表示没有可用计数（不渲染尝试行）。
    :param copilot_id: 最终作业的资源 id；只在载荷里出现，不进标题正文。
    :param copilot_title: 最终作业标题；缺标题时正文写「未命名作业」。
    :param session_id: 会话 id；只在载荷里出现。
    :returns: ``(NotifyEvent.COPILOT_RESEARCH_FINISHED, payload)``。
    """
    canonical = _normalize_result(result)
    label = RESULT_LABELS[canonical]
    stage = _stage_label(stage_code, stage_name)
    used = _attempt_count(attempts_used)
    total = _attempt_count(attempts_total)

    lines = [f"关卡：{stage}"]
    if total > 0:
        lines.append(f"尝试：{used}/{total} 次")
    elif used > 0:
        lines.append(f"尝试：{used} 次")
    title = _text(copilot_title)
    if title:
        lines.append(f"最终作业：{title}")
    elif _text(copilot_id):
        lines.append("最终作业：未命名作业")
    else:
        lines.append("最终作业：无")
    lines.append(RESULT_HINTS[canonical])
    message = "\n".join(lines)

    payload: dict[str, Any] = {
        "title": _title(canonical, label, stage),
        "message": message,
        # 浏览器推送读 body：同一句话，避免渠道与推送两边文案漂移。
        "body": message,
        "result": canonical,
        "result_label": label,
        "stage": stage,
        "stage_id": stage_id,
        "stage_code": stage_code,
        "stage_name": stage_name,
        "attempts_used": used,
        "attempts_total": total,
        "copilot_id": copilot_id,
        "copilot_title": copilot_title,
        "session_id": session_id,
    }
    return NotifyEvent.COPILOT_RESEARCH_FINISHED, payload


def _title(canonical: str, label: str, stage: str) -> str:
    """标题：结果 + 关卡。成功单独成句，其余结果放在括号里（与流水线通知同形）。"""
    if canonical == RESULT_SUCCEEDED:
        return f"作业研究成功：{stage}"
    return f"作业研究结束（{label}）：{stage}"


def _normalize_result(result: Any) -> str:
    """把调用方传来的结果值收敛到七个结果之一；认不出按「出错」处理。"""
    value = result.strip().lower() if isinstance(result, str) else ""
    canonical = _RESULT_ALIASES.get(value)
    if canonical is None:
        logger.warning("作业研究通知收到认不出的结果值 %r，按「出错」发送", result)
        return RESULT_ERROR
    return canonical


def _stage_label(stage_code: str, stage_name: str) -> str:
    """标题里的关卡展示名：代号 + 关卡名，都没有时写「未知关卡」。"""
    code = _text(stage_code)
    name = _text(stage_name)
    if code and name:
        return f"{code} {name}"
    return code or name or "未知关卡"


def _attempt_count(value: Any) -> int:
    """次数按非负整数渲染；布尔与其它类型都当 0（载荷里不出现 ``True``）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
