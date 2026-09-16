"""IPC 协议：命令/事件的类型、payload 结构、超时表与消息构造。

两条单向队列（docs/02 §3、docs/13 ADR-03）：

- ``cmd_queue``（主 → 子）消息固定为 ``{"cmd_id": uuid4, "type": str, "payload": dict}``；
- ``event_queue``（子 → 主）消息固定为 ``{"type": str, "ts": float, "payload": dict}``。

**本模块只依赖标准库，不 import ``maa_api`` 的任何其他模块。** 命令循环跑在
spawn 出来的子进程里，入口模块会被重新 import（M1-02 实测）；零依赖可以避免把
import 期副作用（如旧 ``maa_api.config.config`` 的 mkdir / 拷模板）带进子进程启动
路径，也避免父子解释器之间的导入环。

所有构造器只产出**可 pickle 的纯 dict**（``str`` / ``int`` / ``float`` / ``bool`` /
``None`` / ``list`` / ``dict`` 及其嵌套），不产出 dataclass、枚举实例或任何句柄
对象。``multiprocessing.Queue`` 走 pickle，spawn 模式下不可 pickle 的对象要到运行时
才暴露（docs/03 §8），因此协议层只做纯数据，契约测试用真实 Queue 兜住。

``CALLBACK`` 事件刻意保持 MaaCore 的原始形态 ``{"msg": int, "details": dict}``：
子进程只做 ``json.loads`` 与 ``Message(msg)`` 枚举转换，不翻译语义（docs/02 §3.2），
中文文案映射留在主进程。

超时时长按命令类型区分（docs/02 §3.4）：查询类 5 秒、``CONNECT`` 60 秒、
``LOAD_RESOURCE`` 300 秒、``START`` / ``STOP`` 30 秒；``PING`` 心跳另给短超时。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

__all__ = [
    "COMMAND_TYPES",
    "EVENT_TYPES",
    "REQUIRED_PAYLOAD_KEYS",
    "CMD_TIMEOUTS",
    "DEFAULT_QUERY_TIMEOUT",
    "PING_TIMEOUT",
    "LONG_COMMANDS",
    "make_command",
    "make_event",
    "command_timeout",
    "is_long_command",
    "command_payload",
    "event_payload",
]

#: 全部命令类型，取值与 docs/02 §3.1 的表逐项对应（21 个）。
COMMAND_TYPES: frozenset[str] = frozenset(
    {
        "LOAD_RESOURCE",
        "SET_INSTANCE_OPTION",
        "SET_STATIC_OPTION",
        "SET_CONNECTION_EXTRAS",
        "CONNECT",
        "CONNECTED",
        "APPEND_TASK",
        "SET_TASK_PARAMS",
        "START",
        "STOP",
        "RUNNING",
        "CLICK",
        "SCREENCAP",
        "GET_IMAGE",
        "BACK_TO_HOME",
        "GET_UUID",
        "GET_TASKS_LIST",
        "GET_MAP_LEVEL_KEY",
        "GET_VERSION",
        "PING",
        "SHUTDOWN",
    }
)

#: 全部事件类型，取值与 docs/02 §3.2 的表逐项对应（6 个）。
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "READY",
        "CMD_RESULT",
        "CALLBACK",
        "LOG",
        "PONG",
        "FATAL",
    }
)

#: 每个命令的必填 payload 键（docs/02 §3.1 的 payload 列）。
#: 表里 payload 为「—」的命令（START / STOP / RUNNING / CONNECTED / BACK_TO_HOME /
#: GET_UUID / GET_TASKS_LIST / GET_VERSION）是空元组；``SHUTDOWN`` 的 ``graceful``
#: 在 docs/03 §3.3 的命令循环里是 ``payload.get("graceful", True)``，即有默认值的
#: 可选项，因此也登记为空元组。
REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "LOAD_RESOURCE": ("path", "incremental_paths"),
    "SET_INSTANCE_OPTION": ("key", "value"),
    "SET_STATIC_OPTION": ("key", "value"),
    "SET_CONNECTION_EXTRAS": ("name", "extras"),
    "CONNECT": ("adb_path", "address", "config", "block"),
    "CONNECTED": (),
    "APPEND_TASK": ("type_name", "params"),
    "SET_TASK_PARAMS": ("task_id", "params"),
    "START": (),
    "STOP": (),
    "RUNNING": (),
    "CLICK": ("x", "y", "block"),
    "SCREENCAP": ("block",),
    "GET_IMAGE": ("bgr", "save_to"),
    "BACK_TO_HOME": (),
    "GET_UUID": (),
    "GET_TASKS_LIST": (),
    "GET_MAP_LEVEL_KEY": ("key",),
    "GET_VERSION": (),
    "PING": ("seq",),
    "SHUTDOWN": (),
}

#: 查询类命令的默认超时（秒），docs/02 §3.4。
DEFAULT_QUERY_TIMEOUT: float = 5.0

#: 心跳命令的短超时（秒）：PING 只是往返探活，不该等满查询类的 5 秒。
PING_TIMEOUT: float = 2.0

#: 会长时间阻塞命令循环的命令（docs/02 §3.4），供 M1-08 在慢命令期间放宽心跳。
LONG_COMMANDS: frozenset[str] = frozenset({"LOAD_RESOURCE", "CONNECT"})

#: 命令类型 → 超时秒数。每个命令都有登记值。
CMD_TIMEOUTS: dict[str, float] = {
    **{cmd: DEFAULT_QUERY_TIMEOUT for cmd in sorted(COMMAND_TYPES)},
    "LOAD_RESOURCE": 300.0,
    "CONNECT": 60.0,
    "START": 30.0,
    "STOP": 30.0,
    "PING": PING_TIMEOUT,
}


def make_command(type: str, **payload: Any) -> dict[str, Any]:
    """构造一条命令消息：``{"cmd_id": uuid4, "type": type, "payload": {...}}``。

    未知命令类型或缺少必填 payload 键时抛 :class:`ValueError`（fail loud，
    不静默补默认值，docs/02 §3.1）；额外的 payload 键原样保留。
    """
    if type not in COMMAND_TYPES:
        raise ValueError(f"未知命令类型 {type!r}，合法取值见 COMMAND_TYPES")
    missing = [key for key in REQUIRED_PAYLOAD_KEYS[type] if key not in payload]
    if missing:
        raise ValueError(f"命令 {type} 缺少必填 payload 键 {missing}（docs/02 §3.1）")
    return {"cmd_id": str(uuid.uuid4()), "type": type, "payload": dict(payload)}


def make_event(type: str, **payload: Any) -> dict[str, Any]:
    """构造一条事件消息：``{"type": type, "ts": time.time(), "payload": {...}}``。

    未知事件类型抛 :class:`ValueError`。``ts`` 是 :func:`time.time` 的 float，
    与 docs/02 §3.2 的示例一致。
    """
    if type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型 {type!r}，合法取值见 EVENT_TYPES")
    return {"type": type, "ts": time.time(), "payload": dict(payload)}


def command_timeout(type: str) -> float:
    """返回命令的超时秒数（docs/02 §3.4）：登记值优先，查询类默认 5 秒。

    未知命令类型抛 :class:`KeyError`。
    """
    if type not in COMMAND_TYPES:
        raise KeyError(type)
    return CMD_TIMEOUTS.get(type, DEFAULT_QUERY_TIMEOUT)


def is_long_command(type: str) -> bool:
    """该命令是否会长时间阻塞命令循环（``LOAD_RESOURCE`` / ``CONNECT``）。

    供 M1-08 在慢命令期间放宽心跳判定；未知命令类型抛 :class:`KeyError`。
    """
    if type not in COMMAND_TYPES:
        raise KeyError(type)
    return type in LONG_COMMANDS


def command_payload(command: dict[str, Any]) -> dict[str, Any]:
    """取命令消息的 payload（缺失即 :class:`KeyError`，不吞错）。"""
    return command["payload"]


def event_payload(event: dict[str, Any]) -> dict[str, Any]:
    """取事件消息的 payload（缺失即 :class:`KeyError`，不吞错）。"""
    return event["payload"]
