"""M55：会话历史里 ``tool_calls`` 与 tool 消息的配对巡检。

`scripts/agent_history_audit.py` 是只读巡检：它不参与主链路，所以必须有自己的用例
证明「它真的能发现孤儿」——否则下次出的绿灯毫无意义。用现造的行对象覆盖四种形态：
正常配对、完全缺失、部分缺失（同一条 assistant 里两个调用只回了一个）、
以及 assistant 没有 tool_calls 的控制组。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "agent_history_audit.py"
    spec = importlib.util.spec_from_file_location("agent_history_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_module = _load_module()


def _row(seq: int, role: str, *, calls=None, tool_call_id=None, finish_reason=None):
    return {
        "seq": seq,
        "role": role,
        "tool_calls": None if calls is None else json.dumps(calls, ensure_ascii=False),
        "tool_call_id": tool_call_id,
        "finish_reason": finish_reason,
    }


def _call(call_id: str, name: str = "get_logs"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_paired_history_has_no_orphans() -> None:
    rows = [
        _row(0, "user"),
        _row(1, "assistant", calls=[_call("c1")], finish_reason="tool_calls"),
        _row(2, "tool", tool_call_id="c1"),
        _row(3, "assistant", calls=[_call("c2")], finish_reason="tool_calls"),
        _row(4, "tool", tool_call_id="c2"),
    ]
    assert audit_module.audit_session(rows) == []


def test_missing_reply_is_reported() -> None:
    rows = [
        _row(0, "user"),
        _row(1, "assistant", calls=[_call("c1", "run_copilot_attempt")], finish_reason="tool_calls"),
        # 中断收尾漏补桩消息：库里没有任何 tool_call_id = c1 的行
    ]
    orphans = audit_module.audit_session(rows)
    assert len(orphans) == 1
    assert orphans[0]["seq"] == 1
    assert orphans[0]["call_id"] == "c1"
    assert orphans[0]["name"] == "run_copilot_attempt"


def test_partially_answered_batch_reports_only_the_missing_call() -> None:
    """一批两个调用、只回了一个：只报缺的那个（这是中断窗口最真实的形态）。"""

    rows = [
        _row(0, "user"),
        _row(1, "assistant", calls=[_call("c1"), _call("c2")], finish_reason="tool_calls"),
        _row(2, "tool", tool_call_id="c1"),
    ]
    orphans = audit_module.audit_session(rows)
    assert [o["call_id"] for o in orphans] == ["c2"]


def test_assistant_without_calls_is_ignored() -> None:
    rows = [
        _row(0, "assistant", finish_reason="stop"),
        _row(1, "assistant", calls=[], finish_reason="stop"),
    ]
    assert audit_module.audit_session(rows) == []
