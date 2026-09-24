"""Codex SessionStart(compact) 钩子：上下文压缩后，把 superpowers 执行台账和 git 状态重新注入会话。

只读、不联网；出错时退回一句最小提示，保证不打断会话。需兼容 macOS 自带的 /usr/bin/python3（3.9）。
"""

import json
import subprocess
import sys
from pathlib import Path

LEDGER_TAIL_LINES = 20
STATUS_LINES = 15
FALLBACK = "【上下文刚被压缩】继续之前，先读当前 superpowers 计划的台账和 git log 确认进度，不要凭记忆。"


def git(root, *args):
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def build_context(cwd):
    root = git(cwd, "rev-parse", "--show-toplevel") or cwd
    lines = [
        "【上下文刚被压缩】对话细节已被摘要替代。继续之前，先用下面的台账和 git 记录确认进度，不要凭记忆。",
        "- 如果正在执行 superpowers 计划：重新加载 subagent-driven-development（或 executing-plans）技能，"
        "按它的恢复规则从台账中第一个未完成的任务继续。",
        "- 项目约定见 AGENTS.md。",
        "",
        "当前分支：" + (git(root, "branch", "--show-current") or "（分离 HEAD）"),
        "最近提交：",
        git(root, "log", "--oneline", "-8") or "（无）",
    ]

    status = git(root, "status", "--short").splitlines()
    if status:
        lines.append("未提交改动：")
        lines.extend(status[:STATUS_LINES])
        if len(status) > STATUS_LINES:
            lines.append("……另有 %d 项" % (len(status) - STATUS_LINES))

    ledgers = sorted(
        Path(root, ".superpowers", "sdd").glob("*/progress.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not ledgers:
        lines += ["", "尚无 superpowers 执行台账（.superpowers/sdd/*/progress.md）。"]
        return "\n".join(lines)

    content = ledgers[0].read_text(encoding="utf-8", errors="replace").splitlines()
    lines += [
        "",
        "最近更新的台账：" + str(ledgers[0].relative_to(root)),
        content[0] if content else "（空）",
        "台账最后几行：",
    ]
    lines.extend(content[1:][-LEDGER_TAIL_LINES:])
    if len(ledgers) > 1:
        others = [str(path.relative_to(root)) for path in ledgers[1:4]]
        lines.append("其他台账：" + "、".join(others))
    return "\n".join(lines)


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    try:
        context = build_context(payload.get("cwd") or ".")
    except Exception:
        context = FALLBACK
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
