"""自定义任务链的链级校验（M89 spec §4.5 校验、D15、D24）。

``check_bundle`` 只做「一次注册整条链」需要、而 v2 ``_validate_custom_task``
（单节点字段类型与 JSON 合法性）覆盖不到的检查：

1. 节点数 1–128、``entry`` 存在、每个键匹配 :data:`NAME_PATTERN`；
2. 有 ``action`` 时必须属于 :data:`ALLOWED_ACTIONS`；
3. ``next`` / ``onErrorNext`` / ``exceededNext`` 的引用都在链里，``#self`` 视为环；
4. 从 ``entry`` 深度优先遍历：同路径重复访问报环、遍历不到任何 ``Stop`` 报缺出口、
   ``chain`` 记第一次到达 ``Stop`` 的路径；
5. ``ClickRect`` 缺 ``specificRect``、``Swipe`` 缺 ``specificRect`` / ``rectMove``
   报错；矩形必须落在 1280×720 内且宽高大于 0。

结构错误进 ``errors``，链级错误进 ``chain_errors``；每个错误都带节点名与字段名。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ALLOWED_ACTIONS",
    "MAX_BUNDLE_TASKS",
    "NAME_PATTERN",
    "TaskIssue",
    "check_bundle",
    "check_entry_name",
]

#: 自定义任务名的形状（与 v2 ``_CUSTOM_TASK_NAME`` 同一口径）。
NAME_PATTERN = re.compile(r"^Custom_[A-Za-z0-9][A-Za-z0-9_.-]{0,54}$")

#: 一条链里允许出现的动作（spec §4.5 第 2 条）。
ALLOWED_ACTIONS = frozenset({"ClickSelf", "ClickRect", "DoNothing", "Swipe", "Stop"})

#: 服务层一次注册允许的任务节点上限（工具层另有 32 个的更紧上限）。
MAX_BUNDLE_TASKS = 128

_NEXT_FIELDS = ("next", "onErrorNext", "exceededNext")
_RECT_FIELDS = ("specificRect", "rectMove", "roi")
_SCREEN_WIDTH = 1280
_SCREEN_HEIGHT = 720
_NAME_REASON = "任务名必须以 Custom_ 开头，且只包含字母、数字、点、下划线或连字符"


@dataclass(frozen=True, slots=True)
class TaskIssue:
    """一个校验问题：哪个节点、哪个字段、中文原因。"""

    task: str
    field: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"task": self.task, "field": self.field, "reason": self.reason}


def check_entry_name(name: str) -> list[TaskIssue]:
    """只检查任务名形状；``convert`` 的名字预检与工具层共用。"""
    if isinstance(name, str) and NAME_PATTERN.fullmatch(name):
        return []
    return [TaskIssue(str(name), "name", _NAME_REASON)]


def check_bundle(
    tasks: Mapping[str, Mapping[str, Any]], entry: str
) -> tuple[list[TaskIssue], list[TaskIssue], tuple[str, ...]]:
    """校验整条任务链，返回 ``(errors, chain_errors, chain)``。"""
    errors: list[TaskIssue] = []
    chain_errors: list[TaskIssue] = []
    names = list(tasks)

    if not 1 <= len(names) <= MAX_BUNDLE_TASKS:
        errors.append(
            TaskIssue(
                str(entry),
                "tasks",
                f"任务节点数必须在 1 至 {MAX_BUNDLE_TASKS} 之间",
            )
        )
    for name in names:
        errors.extend(check_entry_name(name))
    if entry not in tasks:
        errors.append(TaskIssue(str(entry), "entry", "入口任务不在任务链里"))
        return errors, chain_errors, ()

    for name in names:
        node = tasks[name]
        if not isinstance(node, Mapping):
            errors.append(TaskIssue(name, "task", "任务节点必须是 JSON 对象"))
            continue
        errors.extend(_node_errors(name, node, tasks, chain_errors))

    chain = _walk_chain(tasks, entry, chain_errors)
    if not chain:
        chain_errors.append(TaskIssue(entry, "chain", "任务链没有 Stop 出口"))
    return errors, chain_errors, chain


# --------------------------------------------------------------------------- 内部


def _node_errors(
    name: str,
    node: Mapping[str, Any],
    tasks: Mapping[str, Mapping[str, Any]],
    chain_errors: list[TaskIssue],
) -> list[TaskIssue]:
    errors: list[TaskIssue] = []
    action = node.get("action")
    if action is not None and (
        not isinstance(action, str) or action not in ALLOWED_ACTIONS
    ):
        errors.append(
            TaskIssue(
                name,
                "action",
                "action 只支持 ClickSelf、ClickRect、DoNothing、Swipe、Stop",
            )
        )

    for field in _NEXT_FIELDS:
        value = node.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            errors.append(TaskIssue(name, field, f"{field} 必须是任务名数组"))
            continue
        for target in value:
            if target == "#self":
                chain_errors.append(
                    TaskIssue(name, field, "任务链有环：#self 指向自身")
                )
            elif target not in tasks:
                chain_errors.append(
                    TaskIssue(name, field, f"引用了不存在的任务 {target}")
                )

    if action == "ClickRect" and "specificRect" not in node:
        errors.append(
            TaskIssue(name, "specificRect", "ClickRect 缺少 specificRect")
        )
    if action == "Swipe":
        for field in ("specificRect", "rectMove"):
            if field not in node:
                errors.append(TaskIssue(name, field, f"Swipe 缺少 {field}"))

    for field in _RECT_FIELDS:
        if field not in node:
            continue
        reason = _rect_error(node[field])
        if reason is not None:
            errors.append(TaskIssue(name, field, f"{field} {reason}"))
    return errors


def _rect_error(value: Any) -> str | None:
    """``[x, y, w, h]`` 必须落在 1280×720 内且宽高大于 0；合法返回 ``None``。"""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return "必须是 [x, y, w, h]"
    if not all(
        isinstance(item, (int, float)) and not isinstance(item, bool)
        for item in value
    ):
        return "的四个分量必须是数字"
    x, y, width, height = (float(item) for item in value)
    if width <= 0 or height <= 0:
        return "的宽高必须大于 0"
    if x < 0 or y < 0 or x + width > _SCREEN_WIDTH or y + height > _SCREEN_HEIGHT:
        return f"的矩形必须落在 {_SCREEN_WIDTH}×{_SCREEN_HEIGHT} 内"
    return None


def _walk_chain(
    tasks: Mapping[str, Mapping[str, Any]],
    entry: str,
    chain_errors: list[TaskIssue],
) -> tuple[str, ...]:
    """从入口深度优先找第一条到 ``Stop`` 的路径；同路径重复访问报环。"""
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()

    def visit(name: str) -> tuple[str, ...] | None:
        if name in path_set:
            chain_errors.append(TaskIssue(name, "next", "任务链有环"))
            return None
        if name in visited:
            return None
        node = tasks.get(name)
        if not isinstance(node, Mapping):
            return None
        visited.add(name)
        path_set.add(name)
        path.append(name)
        if node.get("action") == "Stop":
            return tuple(path)
        for field in _NEXT_FIELDS:
            value = node.get(field)
            if not isinstance(value, list):
                continue
            for target in value:
                if not isinstance(target, str) or target not in tasks:
                    continue
                found = visit(target)
                if found is not None:
                    return found
        path.pop()
        path_set.remove(name)
        return None

    return visit(entry) or ()
