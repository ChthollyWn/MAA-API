"""IPC 协议的契约测试（docs/03 §8）：真实 ``multiprocessing.Queue`` + spawn 的 pickle 往返。

协议层不加载内核、不连设备。本文件钉住四件事：

1. 全部 21 个命令类型与 6 个事件类型各构造一条消息，经 ``get_context("spawn")``
   的真实 ``ctx.Queue()`` 往返，payload 覆盖中文、嵌套 dict/list；
2. 把 Queue 交给真正的 spawn 子进程写、父进程读（另一条用例反向：父进程发命令、
   子进程读并回 CMD_RESULT），证明协议消息在 spawn 模式下可 pickle——不可 pickle
   的对象只在运行时才暴露，契约测试必须提前发现；
3. ``REQUIRED_PAYLOAD_KEYS`` / ``CMD_TIMEOUTS`` 两张表与 docs/02 §3.1 / §3.4 逐项一致；
4. 构造器 fail loud：未知类型、缺必填键抛 ``ValueError``，未知命令查超时抛 ``KeyError``。

契约测试的「两端替身」就是这 21 + 6 条样例消息本身：不 import worker / supervisor /
client（M1-07 / M1-08 / M1-09），只测协议与序列化。
"""

from __future__ import annotations

import ast
import contextlib
import json
import multiprocessing
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from maa_api.core import protocol
from maa_api.core.protocol import (
    CMD_TIMEOUTS,
    COMMAND_TYPES,
    DEFAULT_QUERY_TIMEOUT,
    EVENT_TYPES,
    REQUIRED_PAYLOAD_KEYS,
    command_payload,
    command_timeout,
    event_payload,
    is_long_command,
    make_command,
    make_event,
)

QUEUE_TIMEOUT = 30.0

# ---------------------------------------------------------------------------
# 替身样例：每个命令 / 事件类型一条消息，payload 覆盖中文与嵌套结构
# ---------------------------------------------------------------------------

_COMMAND_SAMPLES: dict[str, dict[str, Any]] = {
    "LOAD_RESOURCE": {
        "path": "resource/",
        "incremental_paths": ["resource/global/zh_CN", "resource/updates/通道B"],
    },
    "SET_INSTANCE_OPTION": {"key": "TouchMode", "value": 2},
    "SET_STATIC_OPTION": {"key": "CpuOCR", "value": False},
    "SET_CONNECTION_EXTRAS": {
        "name": "MuMuEmulator12",
        "extras": {"enable": True, "路径": "/Applications/MuMu.app", "列表": [1, 2]},
    },
    "CONNECT": {
        "adb_path": "/opt/homebrew/bin/adb",
        "address": "127.0.0.1:5555",
        "config": "General",
        "block": False,
    },
    "CONNECTED": {},
    "APPEND_TASK": {
        "type_name": "StartUp",
        "params": {
            "client_type": "Official",
            "嵌套": {"列表": [1, 2.5, None, True], "中文键": "值"},
        },
    },
    "SET_TASK_PARAMS": {"task_id": 1, "params": {"times": 4, "说明": "运行中改参数"}},
    "START": {},
    "STOP": {},
    "RUNNING": {},
    "CLICK": {"x": 640, "y": 360, "block": True},
    "SCREENCAP": {"block": True},
    "GET_IMAGE": {"bgr": False, "save_to": "resource/temp/screencap/测试.png"},
    "BACK_TO_HOME": {},
    "GET_UUID": {},
    "GET_TASKS_LIST": {},
    "GET_MAP_LEVEL_KEY": {"key": "1-7"},
    "GET_VERSION": {},
    "PING": {"seq": 42},
    "SHUTDOWN": {"graceful": True},
}

_EVENT_SAMPLES: dict[str, dict[str, Any]] = {
    "READY": {"version": "v6.17.5", "pid": 4242},
    "CMD_RESULT": {
        "cmd_id": "9f1c2f7e-0d5a-4c3b-8e21-000000000001",
        "ok": True,
        "data": {"task_id": 1, "中文": "返回值", "嵌套": {"列表": [1, 2.5, None]}},
        "error": None,
    },
    "CALLBACK": {
        "msg": 20003,
        "details": {
            "task": "ReturnButton",
            "what": "ExceededLimit",
            "first": ["StartAtHome", "StartWithSanity"],
            "details": {"exec_times": 0},
        },
    },
    "LOG": {"level": "INFO", "content": "子进程日志：资源加载完成"},
    "PONG": {"seq": 42},
    "FATAL": {
        "error": "RuntimeError: 内核调用失败",
        "traceback": "Traceback (most recent call last):\n  File \"worker.py\", line 1\nRuntimeError",
    },
}

_PROTOCOL_MESSAGE_COUNT = len(_COMMAND_SAMPLES) + len(_EVENT_SAMPLES)

_REQUIRED_WITH_PAYLOAD = sorted(t for t in COMMAND_TYPES if REQUIRED_PAYLOAD_KEYS[t])

_EXPECTED_REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
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


def _payload_json(payload: dict[str, Any]) -> str:
    """payload 的规范化 JSON：比 ``==`` 更严格地区分 int / float / bool 与键序。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Queue 夹具与 spawn 子进程入口（target 必须是模块级函数）
# ---------------------------------------------------------------------------


@pytest.fixture
def spawn_ctx():
    """spawn 上下文：macOS / Windows 的默认 start method，也是最严的 pickle 考验。"""
    return multiprocessing.get_context("spawn")


@pytest.fixture
def queue_pair(spawn_ctx) -> Iterator[tuple[Any, Any]]:
    """一对真实的单向队列（cmd_queue 主→子、event_queue 子→主）。"""
    cmd_queue = spawn_ctx.Queue()
    event_queue = spawn_ctx.Queue()
    try:
        yield cmd_queue, event_queue
    finally:
        for queue in (cmd_queue, event_queue):
            queue.close()
            queue.cancel_join_thread()


@contextlib.contextmanager
def _spawn_queue(spawn_ctx) -> Iterator[Any]:
    """单个真实 Queue，退出时关闭并放弃 join（失败路径也不阻塞解释器退出）。"""
    queue = spawn_ctx.Queue()
    try:
        yield queue
    finally:
        queue.close()
        queue.cancel_join_thread()


def _emit_protocol_messages(event_queue) -> None:
    """spawn 子进程入口：把全部协议消息写入 event_queue（子 → 主）。"""
    for type_name in sorted(COMMAND_TYPES):
        event_queue.put(make_command(type_name, **_COMMAND_SAMPLES[type_name]))
    for type_name in sorted(EVENT_TYPES):
        event_queue.put(make_event(type_name, **_EVENT_SAMPLES[type_name]))


def _echo_commands(cmd_queue, event_queue, count: int) -> None:
    """spawn 子进程入口：读 count 条命令，逐条回 CMD_RESULT（主 → 子 → 主）。"""
    for _ in range(count):
        command = cmd_queue.get(timeout=QUEUE_TIMEOUT)
        event_queue.put(
            make_event(
                "CMD_RESULT",
                cmd_id=command["cmd_id"],
                ok=True,
                data={"echo_type": command["type"], "payload_keys": sorted(command["payload"])},
                error=None,
            )
        )


def _join_or_kill(process) -> None:
    process.join(timeout=QUEUE_TIMEOUT)
    if process.is_alive():  # pragma: no cover - 失败保护，正常路径不会走到
        process.kill()
        process.join(timeout=10.0)


# ---------------------------------------------------------------------------
# 1. 覆盖性：样例表必须与协议表逐项对齐
# ---------------------------------------------------------------------------


def test_command_samples_cover_every_command_type() -> None:
    assert set(_COMMAND_SAMPLES) == set(COMMAND_TYPES)
    assert len(COMMAND_TYPES) == 21


def test_event_samples_cover_every_event_type() -> None:
    assert set(_EVENT_SAMPLES) == set(EVENT_TYPES)
    assert len(EVENT_TYPES) == 6


# ---------------------------------------------------------------------------
# 2. 全部命令 / 事件经真实 Queue 的 pickle 往返
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("type_name", sorted(COMMAND_TYPES))
def test_command_pickle_roundtrip_through_real_queue(spawn_ctx, type_name: str) -> None:
    sent = make_command(type_name, **_COMMAND_SAMPLES[type_name])
    with _spawn_queue(spawn_ctx) as queue:
        queue.put(sent)
        got = queue.get(timeout=QUEUE_TIMEOUT)
    assert got == sent
    assert set(got) == {"cmd_id", "type", "payload"}
    assert got["type"] == type_name
    assert isinstance(got["cmd_id"], str) and len(got["cmd_id"]) >= 32
    assert uuid.UUID(got["cmd_id"]).version == 4
    # JSON 规范化比较：int / float / bool 的差异不会被 == 吞掉
    assert _payload_json(got["payload"]) == _payload_json(_COMMAND_SAMPLES[type_name])


@pytest.mark.parametrize("type_name", sorted(EVENT_TYPES))
def test_event_pickle_roundtrip_through_real_queue(spawn_ctx, type_name: str) -> None:
    sent = make_event(type_name, **_EVENT_SAMPLES[type_name])
    with _spawn_queue(spawn_ctx) as queue:
        queue.put(sent)
        got = queue.get(timeout=QUEUE_TIMEOUT)
    assert got == sent
    assert set(got) == {"type", "ts", "payload"}
    assert got["type"] == type_name
    assert isinstance(got["ts"], float)
    assert _payload_json(got["payload"]) == _payload_json(_EVENT_SAMPLES[type_name])


def test_ready_event_payload_roundtrip(spawn_ctx) -> None:
    """READY 的 payload 必须含 version / pid（docs/02 §3.2）。"""
    sent = make_event("READY", version="v6.17.5", pid=4242)
    with _spawn_queue(spawn_ctx) as queue:
        queue.put(sent)
        got = queue.get(timeout=QUEUE_TIMEOUT)
    assert set(got["payload"]) == {"version", "pid"}
    assert got["payload"]["version"] == "v6.17.5"
    assert got["payload"]["pid"] == 4242


def test_cmd_result_error_substructure_roundtrip(spawn_ctx) -> None:
    """CMD_RESULT 的 error 子结构 {code, message} 往返无损（docs/03 §3.3）。"""
    sent = make_event(
        "CMD_RESULT",
        cmd_id=str(uuid.uuid4()),
        ok=False,
        data=None,
        error={"code": "KERNEL_ERROR", "message": "内核调用失败：连接超时"},
    )
    with _spawn_queue(spawn_ctx) as queue:
        queue.put(sent)
        got = queue.get(timeout=QUEUE_TIMEOUT)
    assert got == sent
    assert got["payload"]["ok"] is False
    assert set(got["payload"]["error"]) == {"code", "message"}
    assert got["payload"]["error"]["message"] == "内核调用失败：连接超时"


def test_callback_event_stays_raw(spawn_ctx) -> None:
    """CALLBACK 刻意保持原始形态 {msg: int, details: dict}，不做语义翻译。"""
    sent = make_event("CALLBACK", msg=20003, details=_EVENT_SAMPLES["CALLBACK"]["details"])
    assert set(sent["payload"]) == {"msg", "details"}
    assert isinstance(sent["payload"]["msg"], int)
    with _spawn_queue(spawn_ctx) as queue:
        queue.put(sent)
        got = queue.get(timeout=QUEUE_TIMEOUT)
    assert got == sent


# ---------------------------------------------------------------------------
# 3. spawn 子进程的真实跨进程往返
# ---------------------------------------------------------------------------


def test_spawn_child_writes_all_protocol_messages(spawn_ctx, queue_pair) -> None:
    """子进程写、父进程读：证明 27 条协议消息在 spawn 下都可 pickle。"""
    _, event_queue = queue_pair
    process = spawn_ctx.Process(target=_emit_protocol_messages, args=(event_queue,))
    process.start()
    try:
        received = [event_queue.get(timeout=QUEUE_TIMEOUT) for _ in range(_PROTOCOL_MESSAGE_COUNT)]
    finally:
        _join_or_kill(process)
    assert process.exitcode == 0

    commands = [m for m in received if set(m) == {"cmd_id", "type", "payload"}]
    events = [m for m in received if set(m) == {"type", "ts", "payload"}]
    assert len(commands) == len(COMMAND_TYPES)
    assert len(events) == len(EVENT_TYPES)
    assert {m["type"] for m in commands} == set(COMMAND_TYPES)
    assert {m["type"] for m in events} == set(EVENT_TYPES)
    assert len({m["cmd_id"] for m in commands}) == len(COMMAND_TYPES)
    # 子进程构造的 payload 与父进程样例逐项一致（含中文与嵌套结构）
    for message in commands:
        assert _payload_json(message["payload"]) == _payload_json(_COMMAND_SAMPLES[message["type"]])
    for message in events:
        assert _payload_json(message["payload"]) == _payload_json(_EVENT_SAMPLES[message["type"]])


def test_spawn_child_reads_commands_and_replies(spawn_ctx, queue_pair) -> None:
    """父进程发全部命令、子进程读并逐条回 CMD_RESULT：两条单向队列都过 spawn pickle。"""
    cmd_queue, event_queue = queue_pair
    sent = [make_command(t, **_COMMAND_SAMPLES[t]) for t in sorted(COMMAND_TYPES)]
    process = spawn_ctx.Process(target=_echo_commands, args=(cmd_queue, event_queue, len(sent)))
    process.start()
    try:
        for command in sent:
            cmd_queue.put(command)
        replies = [event_queue.get(timeout=QUEUE_TIMEOUT) for _ in sent]
    finally:
        _join_or_kill(process)
    assert process.exitcode == 0
    assert {r["payload"]["cmd_id"] for r in replies} == {c["cmd_id"] for c in sent}
    assert {r["payload"]["data"]["echo_type"] for r in replies} == set(COMMAND_TYPES)
    assert all(r["type"] == "CMD_RESULT" and r["payload"]["ok"] is True for r in replies)


# ---------------------------------------------------------------------------
# 4. 消息形态、构造器 fail loud 与两张表
# ---------------------------------------------------------------------------


def test_command_message_shape() -> None:
    cmd = make_command("GET_VERSION")
    assert set(cmd) == {"cmd_id", "type", "payload"}
    assert isinstance(cmd["cmd_id"], str) and len(cmd["cmd_id"]) >= 32
    assert uuid.UUID(cmd["cmd_id"]).version == 4
    assert cmd["type"] == "GET_VERSION"
    assert cmd["payload"] == {}
    assert make_command("GET_VERSION")["cmd_id"] != cmd["cmd_id"]


def test_event_message_shape() -> None:
    before = time.time()
    event = make_event("READY", version="v6.17.5", pid=1)
    after = time.time()
    assert set(event) == {"type", "ts", "payload"}
    assert isinstance(event["ts"], float)
    assert before <= event["ts"] <= after
    assert event["payload"] == {"version": "v6.17.5", "pid": 1}


@pytest.mark.parametrize("type_name", _REQUIRED_WITH_PAYLOAD)
def test_make_command_rejects_missing_required_payload(type_name: str) -> None:
    with pytest.raises(ValueError, match="必填"):
        make_command(type_name)


@pytest.mark.parametrize("type_name", sorted(t for t in COMMAND_TYPES if not REQUIRED_PAYLOAD_KEYS[t]))
def test_make_command_accepts_no_payload_commands(type_name: str) -> None:
    assert make_command(type_name)["payload"] == {}


def test_make_command_rejects_partially_missing_payload() -> None:
    """不静默补默认值：CONNECT 缺 block 也要报错（docs/02 §3.1）。"""
    with pytest.raises(ValueError, match="block"):
        make_command("CONNECT", adb_path="/usr/bin/adb", address="127.0.0.1:5555", config="General")
    with pytest.raises(ValueError, match="必填"):
        make_command("CLICK", x=1, y=2)


def test_make_command_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        make_command("NOT_A_COMMAND")


def test_make_command_keeps_extra_payload_keys() -> None:
    """必填表之外的可选键（如 SHUTDOWN.graceful）原样保留。"""
    cmd = make_command("SHUTDOWN", graceful=False, 备注="可选键")
    assert cmd["payload"] == {"graceful": False, "备注": "可选键"}


def test_make_event_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        make_event("NOT_AN_EVENT")


def test_required_payload_keys_table_matches_docs() -> None:
    assert REQUIRED_PAYLOAD_KEYS == _EXPECTED_REQUIRED_PAYLOAD_KEYS
    assert set(REQUIRED_PAYLOAD_KEYS) == set(COMMAND_TYPES)
    assert all(isinstance(keys, tuple) for keys in REQUIRED_PAYLOAD_KEYS.values())


def test_command_timeout_table_matches_docs() -> None:
    assert CMD_TIMEOUTS["LOAD_RESOURCE"] == 300.0
    assert CMD_TIMEOUTS["CONNECT"] == 60.0
    assert CMD_TIMEOUTS["START"] == 30.0
    assert CMD_TIMEOUTS["STOP"] == 30.0
    assert CMD_TIMEOUTS["GET_VERSION"] == DEFAULT_QUERY_TIMEOUT == 5.0
    assert 0 < CMD_TIMEOUTS["PING"] < DEFAULT_QUERY_TIMEOUT
    assert set(CMD_TIMEOUTS) == set(COMMAND_TYPES)
    overridden = {"LOAD_RESOURCE", "CONNECT", "START", "STOP", "PING"}
    for type_name in sorted(COMMAND_TYPES - overridden):
        assert command_timeout(type_name) == 5.0


def test_command_timeout_unknown_type_raises_key_error() -> None:
    with pytest.raises(KeyError):
        command_timeout("NOT_A_COMMAND")


@pytest.mark.parametrize(
    ("type_name", "expected"),
    [
        ("LOAD_RESOURCE", True),
        ("CONNECT", True),
        ("APPEND_TASK", False),
        ("START", False),
        ("GET_VERSION", False),
    ],
)
def test_is_long_command(type_name: str, expected: bool) -> None:
    assert is_long_command(type_name) is expected


def test_is_long_command_unknown_type_raises_key_error() -> None:
    with pytest.raises(KeyError):
        is_long_command("NOT_A_COMMAND")


def test_payload_accessors() -> None:
    assert command_payload(make_command("PING", seq=1)) == {"seq": 1}
    assert event_payload(make_event("PONG", seq=1)) == {"seq": 1}
    with pytest.raises(KeyError):
        event_payload({"type": "PONG"})
    with pytest.raises(KeyError):
        command_payload({"type": "PING"})


def test_protocol_module_only_imports_stdlib() -> None:
    """零 maa_api 依赖：spawn 子进程 import 协议模块不能带进任何 import 副作用。"""
    source = Path(protocol.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "maa_api" not in imported
    assert imported <= {"__future__", "time", "uuid", "typing"}, imported
