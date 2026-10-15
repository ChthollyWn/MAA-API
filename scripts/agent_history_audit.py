#!/usr/bin/env python
"""巡检：会话历史里的 ``tool_calls`` 与 tool 消息是否一一配对（M55）。

为什么需要它：协议要求 assistant 的每条 ``tool_calls`` 都有配对的 tool 消息，
缺一条，下一轮请求就会被上游拒绝（``runtime.py`` 的中断收尾注释写着这条硬要求）。
中断、超时、崩溃都可能在这个窗口上留下孤儿，而这类损伤**不会自己报错**——它在下一次
请求时才炸，现场已经过去了。

用法：

    .venv/bin/python scripts/agent_history_audit.py                 # 全部会话
    .venv/bin/python scripts/agent_history_audit.py <session_id>    # 只看一个会话
    .venv/bin/python scripts/agent_history_audit.py --json          # 机器可读

退出码：0 = 没有孤儿；1 = 发现孤儿（并逐条打印 seq 与 call id）。
只读：只查库，不写任何东西。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "resource" / "maa_api.db"


def audit_session(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """返回该会话里的孤儿调用：``[{seq, call_id, name, finish_reason}]``。"""

    replies = {row["tool_call_id"] for row in rows if row["tool_call_id"]}
    orphans: list[dict[str, Any]] = []
    for row in rows:
        if row["role"] != "assistant" or not row["tool_calls"]:
            continue
        try:
            calls = json.loads(row["tool_calls"])
        except (TypeError, ValueError):
            continue
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else None
            name = ((call or {}).get("function") or {}).get("name") if isinstance(call, dict) else None
            if not call_id or call_id in replies:
                continue
            orphans.append(
                {
                    "seq": row["seq"],
                    "call_id": call_id,
                    "name": name,
                    "finish_reason": row["finish_reason"],
                }
            )
    return orphans


def audit(db_path: Path, session_id: str | None) -> dict[str, Any]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        if session_id:
            sessions = [session_id]
        else:
            sessions = [
                row["session_id"]
                for row in connection.execute(
                    "select distinct session_id from agent_message order by session_id"
                )
            ]
        report: dict[str, Any] = {"db": str(db_path), "sessions": 0, "messages": 0, "orphans": []}
        for sid in sessions:
            rows = list(
                connection.execute(
                    "select seq, role, tool_calls, tool_call_id, finish_reason from agent_message "
                    "where session_id = ? order by seq",
                    (sid,),
                )
            )
            report["sessions"] += 1
            report["messages"] += len(rows)
            for orphan in audit_session(rows):
                report["orphans"].append({"session_id": sid, **orphan})
        return report
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="会话历史 tool_calls 配对巡检（只读）")
    parser.add_argument("session_id", nargs="?", default=None)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"找不到数据库：{db_path}", file=sys.stderr)
        return 2

    report = audit(db_path, args.session_id)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"会话 {report['sessions']} 个、消息 {report['messages']} 条，"
            f"孤儿 tool_calls {len(report['orphans'])} 条"
        )
        for orphan in report["orphans"]:
            print(
                f"  {orphan['session_id']} seq={orphan['seq']} "
                f"call={orphan['call_id']} name={orphan['name']} "
                f"finish={orphan['finish_reason']}"
            )
    return 1 if report["orphans"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
