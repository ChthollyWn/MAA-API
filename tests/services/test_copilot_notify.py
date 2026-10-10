"""``copilot_research_finished`` 的事件与载荷构造（M29，规格 §9.9）。

断言四件事：

- 事件固定是 ``NotifyEvent.COPILOT_RESEARCH_FINISHED``，载荷带规格要求的关卡、
  结果、已用/总次数、最终作业 id 与标题、会话 id；
- 结果口径的七个中文短句（成功 / 次数用完 / 理智不足 / 出错 / 已撤销 / 已过期 /
  被中断），以及调用方可能传的别名（授权状态、尝试结果）都能落到这七个值上；
- 标题与正文是人话：不出现 ``asset-`` / ``sess-`` 这类内部标识符，正文同时写进
  ``message``（渠道）与 ``body``（浏览器推送），两条路径共用一份载荷；
- 载荷经 ``NotifyService`` 渲染时标题与正文原样可用。
"""

import logging

import pytest

from maa_api.domain.enums import NotifyEvent
from maa_api.services.copilot.notify import (
    RESULT_ERROR,
    RESULT_EXHAUSTED,
    RESULT_EXPIRED,
    RESULT_INTERRUPTED,
    RESULT_LABELS,
    RESULT_REVOKED,
    RESULT_SANITY_INSUFFICIENT,
    RESULT_SUCCEEDED,
    build_copilot_research_notification,
)

#: 一次完整的研究：2/3 次尝试、三星通关、最终作业是第三版草稿。
FULL = dict(
    result=RESULT_SUCCEEDED,
    stage_code="1-7",
    stage_name="苦难摇篮",
    stage_id="main_01-07",
    attempts_used=2,
    attempts_total=3,
    copilot_id="asset-abcdef",
    copilot_title="1-7 三星作业",
    session_id="sess-abcdef",
)


# ---------------------------------------------------------------------------
# 事件与字段
# ---------------------------------------------------------------------------
def test_event_and_payload_fields() -> None:
    event, payload = build_copilot_research_notification(**FULL)

    assert event is NotifyEvent.COPILOT_RESEARCH_FINISHED
    assert event.value == "copilot_research_finished"
    assert payload["result"] == RESULT_SUCCEEDED
    assert payload["result_label"] == "成功"
    assert payload["stage_code"] == "1-7"
    assert payload["stage_name"] == "苦难摇篮"
    assert payload["stage_id"] == "main_01-07"
    assert payload["stage"] == "1-7 苦难摇篮"
    assert payload["attempts_used"] == 2
    assert payload["attempts_total"] == 3
    assert payload["copilot_id"] == "asset-abcdef"
    assert payload["copilot_title"] == "1-7 三星作业"
    assert payload["session_id"] == "sess-abcdef"
    assert payload["title"].startswith("作业研究成功")
    assert "1-7 苦难摇篮" in payload["title"]


def test_seven_results_have_chinese_labels() -> None:
    assert RESULT_LABELS == {
        RESULT_SUCCEEDED: "成功",
        RESULT_EXHAUSTED: "次数用完",
        RESULT_SANITY_INSUFFICIENT: "理智不足",
        RESULT_ERROR: "出错",
        RESULT_REVOKED: "已撤销",
        RESULT_EXPIRED: "已过期",
        RESULT_INTERRUPTED: "被中断",
    }


@pytest.mark.parametrize(
    ("result", "label"),
    [
        (RESULT_SUCCEEDED, "成功"),
        (RESULT_EXHAUSTED, "次数用完"),
        (RESULT_SANITY_INSUFFICIENT, "理智不足"),
        (RESULT_ERROR, "出错"),
        (RESULT_REVOKED, "已撤销"),
        (RESULT_EXPIRED, "已过期"),
        (RESULT_INTERRUPTED, "被中断"),
    ],
)
def test_each_result_renders_its_label(result: str, label: str) -> None:
    event, payload = build_copilot_research_notification(**{**FULL, "result": result})

    assert event is NotifyEvent.COPILOT_RESEARCH_FINISHED
    assert payload["result"] == result
    assert payload["result_label"] == label
    assert label in payload["title"]
    assert payload["message"].splitlines()[-1]  # 每种结果都有一句后续说明


# ---------------------------------------------------------------------------
# 别名与兜底
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("success", RESULT_SUCCEEDED),
        ("succeeded", RESULT_SUCCEEDED),
        ("three_star", RESULT_SUCCEEDED),
        ("exhausted", RESULT_EXHAUSTED),
        ("used_up", RESULT_EXHAUSTED),
        ("stopped", RESULT_SANITY_INSUFFICIENT),  # 理智不足是 stopped 的主要成因
        ("sanity_insufficient", RESULT_SANITY_INSUFFICIENT),
        ("no_sanity", RESULT_SANITY_INSUFFICIENT),
        ("failed", RESULT_ERROR),
        ("error", RESULT_ERROR),
        ("revoked", RESULT_REVOKED),
        ("cancelled", RESULT_REVOKED),
        ("expired", RESULT_EXPIRED),
        ("timeout", RESULT_EXPIRED),
        ("interrupted", RESULT_INTERRUPTED),
        ("NOT_A_RESULT", RESULT_ERROR),
        ("", RESULT_ERROR),
    ],
)
def test_result_aliases_normalize(given: str, expected: str) -> None:
    _event, payload = build_copilot_research_notification(
        result=given, stage_code="1-7"
    )

    assert payload["result"] == expected


def test_unknown_result_is_logged_and_still_sent(caplog: pytest.LogCaptureFixture) -> None:
    """通知不能因为一个认不出的结果值就发不出去；记一条 warning 即可。"""
    with caplog.at_level(logging.WARNING, logger="maa_api.services.copilot.notify"):
        event, payload = build_copilot_research_notification(
            result="weird_value", stage_code="1-7"
        )

    assert event is NotifyEvent.COPILOT_RESEARCH_FINISHED
    assert payload["result"] == RESULT_ERROR
    assert "weird_value" in caplog.text


# ---------------------------------------------------------------------------
# 人话：标题、正文与推送
# ---------------------------------------------------------------------------
def test_message_is_chinese_and_has_no_internal_identifiers() -> None:
    _event, payload = build_copilot_research_notification(**FULL)

    assert payload["message"] == (
        "关卡：1-7 苦难摇篮\n"
        "尝试：2/3 次\n"
        "最终作业：1-7 三星作业\n"
        "已达成成功标准，这一关的研究可以结束了。"
    )
    assert "asset-abcdef" not in payload["message"]
    assert "sess-abcdef" not in payload["message"]
    assert "asset-abcdef" not in payload["title"]


def test_body_mirrors_message_for_browser_push() -> None:
    """同一份载荷既喂通知渠道（``message``）也喂 Web Push（``body``）。"""
    _event, payload = build_copilot_research_notification(**FULL)

    assert payload["body"] == payload["message"]


def test_no_final_copilot_and_no_attempts() -> None:
    """授权过期且一次都没跑成：没有最终作业，也没有尝试行。"""
    _event, payload = build_copilot_research_notification(
        result=RESULT_EXPIRED, stage_code="1-7"
    )

    assert payload["copilot_id"] == ""
    assert payload["copilot_title"] == ""
    assert payload["message"].splitlines() == [
        "关卡：1-7",
        "最终作业：无",
        "授权窗口已过期，剩余次数作废，本次研究结束。",
    ]
    assert "尝试" not in payload["message"]


def test_unnamed_final_copilot_does_not_leak_id() -> None:
    _event, payload = build_copilot_research_notification(
        result=RESULT_EXHAUSTED,
        stage_code="1-7",
        attempts_used=3,
        attempts_total=3,
        copilot_id="asset-abcdef",
    )

    assert "最终作业：未命名作业" in payload["message"]
    assert "asset-abcdef" not in payload["message"]


def test_stage_label_falls_back_to_code_then_unknown() -> None:
    _event, payload = build_copilot_research_notification(
        result=RESULT_REVOKED, stage_name="苦难摇篮"
    )
    assert payload["stage"] == "苦难摇篮"
    assert payload["title"].endswith("苦难摇篮")

    _event, payload = build_copilot_research_notification(result=RESULT_REVOKED)
    assert payload["stage"] == "未知关卡"
    assert payload["title"].endswith("未知关卡")


def test_negative_attempt_counts_are_clamped() -> None:
    _event, payload = build_copilot_research_notification(
        result=RESULT_ERROR, attempts_used=-2, attempts_total=-3
    )

    assert payload["attempts_used"] == 0
    assert payload["attempts_total"] == 0


# ---------------------------------------------------------------------------
# 通知渠道的默认中文名
# ---------------------------------------------------------------------------
def test_notify_service_has_chinese_default_label() -> None:
    from maa_api.services.notify_service import _message

    title, body = _message(NotifyEvent.COPILOT_RESEARCH_FINISHED, {})

    assert title == "作业研究结束"
    assert body == "点开 MAA-API 查看详情"


def test_notify_service_renders_builder_payload() -> None:
    """载荷经 ``NotifyService`` 渲染：标题与正文都是构造好的中文。"""
    from maa_api.services.notify_service import _message

    event, payload = build_copilot_research_notification(**FULL)
    title, body = _message(event, payload)

    assert title == payload["title"]
    assert body == payload["message"]
