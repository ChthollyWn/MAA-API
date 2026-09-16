"""CoreClient 的消费线程、cmd_id→Future、差异化超时、两级 Future 与事件分派测试（M1-09）。

不碰真实内核与设备：IPC 用 ``multiprocessing.get_context("spawn")`` 的真实 Queue，一条
模块级辅助线程（:func:`_echo_loop`）充当「子进程命令循环」——读 ``cmd_queue`` 回
``CMD_RESULT``；``CALLBACK`` 由用例自己投进 ``event_queue``，便于精确控制时序。假
supervisor（:class:`_FakeSupervisor`）只实现 ``CoreClient`` 依赖的
``cmd_queue`` / ``event_queue`` / ``state`` / ``track_command`` / ``release_command``
（外加 ``set_dispatcher`` / ``note_event`` 记录调用）。

仓库没有 pytest-asyncio，异步用例用 ``asyncio.run(...)`` 包在同步 test 里；所有等待都有
≤5 秒上限；每个用例结束由 ``ipc`` fixture 兜底 ``client.close()``、停 echo 线程、关闭
真实 Queue。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import multiprocessing
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from maa_api.core.client import CoreClient
from maa_api.core.enums import Message
from maa_api.core.supervisor import CoreState
from maa_api.domain.errors import AppError, ErrorCode

REPO_ROOT = Path(__file__).resolve().parents[2]
ASYNC_SAMPLE_PATH = REPO_ROOT / "tests" / "fixtures" / "async_call_info_sample.json"

#: 所有等待的上限（秒）：用例纪律要求 ≤5 秒。
WAIT_TIMEOUT = 5.0

#: echo 线程默认的 CONNECTED 应答序列（最后一个值会被重复使用）。
DEFAULT_CONNECTED_ANSWERS = [True]


# ---------------------------------------------------------------------------
# 实测样本 / 假 supervisor / echo 线程 / 夹具
# ---------------------------------------------------------------------------


def _load_async_sample() -> dict:
    """M1-01 真机实测的 AsyncCallInfo 载荷（回调事件 payload 的 ``details`` 字段）。

    ``async_call_info_sample.json`` 里 ``async_call_info.details`` 就是子进程
    ``CALLBACK`` 事件 payload 的 ``details``：顶层 ``async_call_id`` / ``what``，
    成功标志在 ``details.ret``（即事件 payload 的 ``details.details.ret``）。
    """
    with ASYNC_SAMPLE_PATH.open(encoding="utf-8") as fp:
        sample = json.load(fp)
    info = sample["async_call_info"]["details"]
    assert isinstance(info, dict) and "async_call_id" in info, info
    return info


class _FakeSupervisor:
    """只含 CoreClient 依赖成员的假 supervisor（外加两个可选的记录方法）。"""

    def __init__(self, cmd_queue: Any, event_queue: Any, state: CoreState = CoreState.READY):
        self.cmd_queue = cmd_queue
        self.event_queue = event_queue
        self.state = state
        self.tracked: list[dict] = []
        self.released: list[str] = []
        self.dispatchers: list[Callable[..., Any]] = []
        self.noted_events: list[dict] = []

    def set_dispatcher(self, fn: Callable[..., Any]) -> None:
        self.dispatchers.append(fn)

    def track_command(self, command: dict) -> None:
        self.tracked.append(command)

    def release_command(self, cmd_id: str) -> None:
        self.released.append(cmd_id)

    def note_event(self, event: dict) -> None:
        self.noted_events.append(event)


def _reply_data(type_name: str, payload: dict, config: dict, counters: dict) -> Any:
    """按命令类型给出 ``CMD_RESULT.data``（不碰内核的假回执）。"""
    if type_name == "CONNECTED":
        answers = config["connected_answers"]
        index = min(counters["connected"], len(answers) - 1)
        counters["connected"] += 1
        return bool(answers[index])
    if type_name in ("CONNECT", "CLICK", "SCREENCAP"):
        # 第一级只回受理编号；第二级结果由用例投 msg=4 的 CALLBACK 决定。
        return {"async_call_id": config["async_call_id"]}
    if type_name == "APPEND_TASK":
        return 1
    if type_name == "SET_TASK_PARAMS":
        return True
    if type_name in ("START", "STOP", "BACK_TO_HOME"):
        return True
    if type_name == "RUNNING":
        return False
    if type_name == "GET_VERSION":
        return "v6.17.5"
    if type_name == "GET_UUID":
        return "f7c1c4ced5e96a23"
    if type_name == "GET_TASKS_LIST":
        return []
    if type_name == "GET_MAP_LEVEL_KEY":
        return {"stage_id": "main_01-07#f#", "code": payload.get("key"), "name": "暴君"}
    if type_name == "GET_IMAGE":
        return {"path": payload["save_to"], "size": 3, "encoding": "raw"}
    return True


def _echo_loop(cmd_queue: Any, event_queue: Any, stop_event: threading.Event, config: dict) -> None:
    """辅助线程：模拟子进程命令循环，读 ``cmd_queue`` 逐条回 ``CMD_RESULT``。

    ``config`` 键：``drop``（故意不回结果的命令类型集合）、``connected_answers``
    （CONNECTED 的依次应答）、``async_call_id``（异步调用的受理编号）。
    """
    counters = {"connected": 0}
    while not stop_event.is_set():
        try:
            command = cmd_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        except (OSError, ValueError, EOFError):
            return
        if not isinstance(command, dict):
            continue
        type_name = command.get("type")
        payload = command.get("payload") or {}
        if type_name == "PING" or type_name in config["drop"]:
            continue  # 心跳与「故意不回」的命令：保持待决，让用例观察超时
        delay = config.get("delay_before_reply", {}).get(type_name, 0.0)
        if delay and stop_event.wait(delay):
            return
        ok, data, error = True, _reply_data(type_name, payload, config, counters), None
        event_queue.put(
            {
                "type": "CMD_RESULT",
                "ts": time.time(),
                "payload": {
                    "cmd_id": command.get("cmd_id"),
                    "ok": ok,
                    "data": data,
                    "error": error,
                },
            }
        )


class _Harness:
    """真实 Queue + echo 线程 + 假 supervisor + 若干 CoreClient 的测试台。"""

    def __init__(
        self,
        cmd_queue: Any,
        event_queue: Any,
        config: dict,
        supervisor: _FakeSupervisor,
        stop_event: threading.Event,
        echo_thread: threading.Thread,
    ) -> None:
        self.cmd_queue = cmd_queue
        self.event_queue = event_queue
        self.config = config
        self.supervisor = supervisor
        self._stop_event = stop_event
        self._echo_thread = echo_thread
        self._clients: list[CoreClient] = []

    def make_client(self, **kwargs: Any) -> CoreClient:
        options: dict[str, Any] = {
            "poll_interval": 0.05,
            "connect_timeout": 2.0,
            "accept_timeout": 0.5,
        }
        options.update(kwargs)
        client = CoreClient(self.supervisor, **options)
        self._clients.append(client)
        return client

    def close(self) -> None:
        for client in self._clients:
            client.close()
        self._stop_event.set()
        self._echo_thread.join(timeout=2.0)
        for real_queue in (self.cmd_queue, self.event_queue):
            with contextlib.suppress(Exception):
                real_queue.close()
            with contextlib.suppress(Exception):
                real_queue.cancel_join_thread()


@pytest.fixture
def ipc() -> Iterator[_Harness]:
    """真实 spawn Queue + echo 线程的测试台（用例结束统一清理，无残留线程）。"""
    ctx = multiprocessing.get_context("spawn")
    cmd_queue = ctx.Queue()
    event_queue = ctx.Queue()
    stop_event = threading.Event()
    config: dict = {
        "drop": set(),
        "connected_answers": list(DEFAULT_CONNECTED_ANSWERS),
        "async_call_id": 1,
        "delay_before_reply": {},
    }
    echo_thread = threading.Thread(
        target=_echo_loop,
        args=(cmd_queue, event_queue, stop_event, config),
        name="test-echo",
        daemon=True,
    )
    echo_thread.start()
    supervisor = _FakeSupervisor(cmd_queue, event_queue)
    harness = _Harness(cmd_queue, event_queue, config, supervisor, stop_event, echo_thread)
    try:
        yield harness
    finally:
        harness.close()


async def _wait_until(
    predicate: Callable[[], bool], what: str, timeout: float = WAIT_TIMEOUT
) -> None:
    """轮询等待（上限 ``timeout`` 秒）；超时抛带说明的 AssertionError。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待超时（{timeout}s）：{what}")


# ---------------------------------------------------------------------------
# ① cmd_id → Future 正常往返 + 事件分派
# ---------------------------------------------------------------------------


def test_cmd_round_trip_and_event_dispatch(ipc: _Harness, caplog: pytest.LogCaptureFixture) -> None:
    """① 命令经真实 Queue 往返兑现 Future；分派按注册顺序，处理器异常不打断消费。"""
    client = ipc.make_client()
    supervisor = ipc.supervisor
    seen: list[tuple[str, dict]] = []
    client.on("LOG", lambda payload: seen.append(("log", payload)))
    client.on("READY", lambda payload: seen.append(("ready", payload)))

    def boom(payload: dict) -> None:
        raise RuntimeError("处理器故意抛异常")

    client.on("LOG", boom)

    async def scenario() -> None:
        client.start_consumer()
        client.start_consumer()  # 幂等：不应起第二条线程
        assert await client.connected() is True
        assert await client.append_task("StartUp", {"client_type": "Official"}) == 1
        assert await client.get_version() == "v6.17.5"
        assert await client.running() is False
        assert await client.resolve_stage("1-7") == {
            "stage_id": "main_01-07#f#",
            "code": "1-7",
            "name": "暴君",
        }
        ipc.event_queue.put(
            {"type": "LOG", "ts": time.time(), "payload": {"level": "INFO", "content": "hello"}}
        )
        ipc.event_queue.put(
            {
                "type": "READY",
                "ts": time.time(),
                "payload": {"version": "v6.17.5", "pid": 4321},
            }
        )
        await _wait_until(lambda: len(seen) >= 2, "LOG / READY 事件分派到注册的处理器")

    with caplog.at_level(logging.ERROR, logger="maa_api.core.client"):
        asyncio.run(scenario())

    assert [kind for kind, _ in seen] == ["log", "ready"]
    assert len(supervisor.dispatchers) == 1  # start_consumer 幂等
    assert "处理器故意抛异常" in caplog.text  # 异常被吞掉并记日志
    assert [command["type"] for command in supervisor.tracked] == [
        "CONNECTED",
        "APPEND_TASK",
        "GET_VERSION",
        "RUNNING",
        "GET_MAP_LEVEL_KEY",
    ]
    assert len(supervisor.released) == 5
    assert client._pending_cmds == {}


# ---------------------------------------------------------------------------
# ② 差异化超时 ③ 迟到结果
# ---------------------------------------------------------------------------


def test_command_timeout_raises_and_releases(ipc: _Harness) -> None:
    """② 命令超时抛 ``CORE_COMMAND_TIMEOUT``，pop 待决表并 ``release_command``。"""
    client = ipc.make_client()
    supervisor = ipc.supervisor
    ipc.config["drop"].add("GET_UUID")

    async def scenario() -> None:
        client.start_consumer()
        with pytest.raises(AppError) as excinfo:
            await client._send("GET_UUID", {}, timeout=0.2)
        assert excinfo.value.code == ErrorCode.CORE_COMMAND_TIMEOUT
        assert "GET_UUID" in excinfo.value.message
        # 一条命令超时不影响后续命令（待决表已清理干净）。
        assert await client.get_version() == "v6.17.5"

    asyncio.run(scenario())

    assert client._pending_cmds == {}
    assert supervisor.tracked[0]["type"] == "GET_UUID"
    assert supervisor.released[0] == supervisor.tracked[0]["cmd_id"]


def test_late_cmd_result_is_dropped_with_warning(
    ipc: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """③ 超时后迟到的 CMD_RESULT 被丢弃并记 warning，不 set 到已结束的 Future。"""
    client = ipc.make_client()
    supervisor = ipc.supervisor
    ipc.config["drop"].add("GET_UUID")
    results: list[dict] = []
    client.on("CMD_RESULT", lambda payload: results.append(payload))

    async def scenario() -> None:
        client.start_consumer()
        with pytest.raises(AppError) as excinfo:
            await client._send("GET_UUID", {}, timeout=0.2)
        assert excinfo.value.code == ErrorCode.CORE_COMMAND_TIMEOUT
        cmd_id = supervisor.tracked[-1]["cmd_id"]
        ipc.event_queue.put(
            {
                "type": "CMD_RESULT",
                "ts": time.time(),
                "payload": {"cmd_id": cmd_id, "ok": True, "data": "late-uuid", "error": None},
            }
        )
        await _wait_until(lambda: bool(results), "迟到的 CMD_RESULT 到达分派表")
        assert client._pending_cmds == {}
        assert "迟到" in caplog.text and cmd_id in caplog.text
        # 消费循环没有被迟到结果打断。
        assert await client.get_version() == "v6.17.5"

    with caplog.at_level(logging.WARNING, logger="maa_api.core.client"):
        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ④ 未就绪门禁
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (CoreState.STARTING, ErrorCode.CORE_NOT_READY),
        (CoreState.STOPPED, ErrorCode.CORE_NOT_READY),
        (CoreState.RESTARTING, ErrorCode.CORE_RESTARTING),
        (CoreState.CRASHED, ErrorCode.CORE_CRASHED),
        (CoreState.FAILED, ErrorCode.CORE_START_FAILED),
    ],
)
def test_not_ready_gate_rejects_without_queueing(
    ipc: _Harness, state: CoreState, expected: ErrorCode
) -> None:
    """④ 未就绪各态抛对应错误码，``details["state"]`` 带状态且命令不进队列。"""
    client = ipc.make_client()
    supervisor = ipc.supervisor
    supervisor.state = state

    async def scenario() -> None:
        client.start_consumer()
        for method, args in (
            ("connected", ()),
            ("get_version", ()),
            ("append_task", ("StartUp", {})),
        ):
            with pytest.raises(AppError) as excinfo:
                await getattr(client, method)(*args)
            assert excinfo.value.code == expected
            assert excinfo.value.details["state"] == state.value
            assert excinfo.value.details["type"] != ""

    asyncio.run(scenario())

    assert supervisor.tracked == []
    assert ipc.cmd_queue.empty()
    assert client._pending_cmds == {}


# ---------------------------------------------------------------------------
# ⑤ 两级 Future ⑥ 解析失败回退
# ---------------------------------------------------------------------------


def test_connect_two_level_future_with_measured_sample(ipc: _Harness) -> None:
    """⑤ CONNECT 先回 async_call_id，再由 msg=4 的 AsyncCallInfo 决定结果。

    载荷直接用 M1-01 真机实测样本（顶层 ``async_call_id`` + ``details.ret``）；
    msg=2 的 ConnectionInfo **不得**兑现 Future。回退路径被配置成「一直未连接」，
    因此只有回调路径才能让 ``connect()`` 返回 True。
    """
    client = ipc.make_client()
    supervisor = ipc.supervisor
    sample = _load_async_sample()
    call_id = int(sample["async_call_id"])
    ipc.config["async_call_id"] = call_id
    ipc.config["connected_answers"] = [False]

    async def scenario() -> None:
        client.start_consumer()
        task = asyncio.create_task(
            client.connect("/opt/homebrew/bin/adb", "127.0.0.1:5555", timeout=2.0)
        )
        await _wait_until(
            lambda: call_id in client._pending_async, "CONNECT 受理结果登记第二级 Future"
        )
        ipc.event_queue.put(
            {
                "type": "CALLBACK",
                "ts": time.time(),
                "payload": {
                    "msg": int(Message.ConnectionInfo),
                    "details": {
                        "adb": "/opt/homebrew/bin/adb",
                        "address": "127.0.0.1:5555",
                        "config": "General",
                        "what": "Connected",
                        "why": "",
                    },
                },
            }
        )
        await asyncio.sleep(0.2)
        assert not task.done(), "ConnectionInfo 的 Connected 不得兑现两级 Future"
        ipc.event_queue.put(
            {
                "type": "CALLBACK",
                "ts": time.time(),
                "payload": {"msg": int(Message.AsyncCallInfo), "details": sample},
            }
        )
        assert await asyncio.wait_for(task, WAIT_TIMEOUT) is True
        assert client._pending_async == {}

    asyncio.run(scenario())

    assert [command["type"] for command in supervisor.tracked] == ["CONNECT"]


@pytest.mark.parametrize(
    "malformed",
    [
        {"what": "Connect", "details": {"cost": 1632}},  # 缺 async_call_id
        {"async_call_id": 1, "what": "Connect", "details": {"cost": 1632}},  # 缺结果字段
    ],
)
def test_connect_parse_failure_falls_back_to_connected_polling(
    ipc: _Harness, caplog: pytest.LogCaptureFixture, malformed: dict
) -> None:
    """⑥ 取不到字段：记完整原文 warning，回退 CONNECTED 轮询判定连接状态。"""
    client = ipc.make_client()
    supervisor = ipc.supervisor
    ipc.config["async_call_id"] = 1
    ipc.config["connected_answers"] = [False, True]

    async def scenario() -> None:
        client.start_consumer()
        task = asyncio.create_task(
            client.connect("/opt/homebrew/bin/adb", "127.0.0.1:5555", timeout=2.0)
        )
        await _wait_until(
            lambda: 1 in client._pending_async, "CONNECT 受理结果登记第二级 Future"
        )
        ipc.event_queue.put(
            {
                "type": "CALLBACK",
                "ts": time.time(),
                "payload": {"msg": int(Message.AsyncCallInfo), "details": malformed},
            }
        )
        assert await asyncio.wait_for(task, WAIT_TIMEOUT) is True
        assert client._pending_async == {}
        assert "完整原文" in caplog.text
        assert "1632" in caplog.text  # 完整原文（不是只记几个键名）

    with caplog.at_level(logging.WARNING, logger="maa_api.core.client"):
        asyncio.run(scenario())

    types = [command["type"] for command in supervisor.tracked]
    assert types[0] == "CONNECT"
    assert types.count("CONNECTED") >= 2  # 先 False 后 True：确实走了轮询


def test_connect_result_timeout_pops_pending_async(ipc: _Harness) -> None:
    """两级 Future 的第二级超时：抛 ``CORE_COMMAND_TIMEOUT`` 并清掉待决表。"""
    client = ipc.make_client()
    ipc.config["async_call_id"] = 1

    async def scenario() -> None:
        client.start_consumer()
        with pytest.raises(AppError) as excinfo:
            await client.connect("/opt/homebrew/bin/adb", "127.0.0.1:5555", timeout=0.2)
        assert excinfo.value.code == ErrorCode.CORE_COMMAND_TIMEOUT
        assert client._pending_async == {}

    asyncio.run(scenario())


def test_async_callback_before_acceptance_is_buffered(ipc: _Harness) -> None:
    """回调先于 CMD_RESULT 到达：结果进缓存，受理结果到达时立即兑现。

    这是真实存在的时序（回调在内核回调线程上发出，``CMD_RESULT`` 要等命令循环回到
    循环顶部才入队；``FakeAsst.connect_async`` 就是同步发回调），不缓存会让
    ``connect()`` 白等一个完整超时。
    """
    client = ipc.make_client()
    sample = _load_async_sample()
    ipc.config["async_call_id"] = int(sample["async_call_id"])
    ipc.config["delay_before_reply"] = {"CONNECT": 0.3}

    async def scenario() -> None:
        client.start_consumer()
        task = asyncio.create_task(
            client.connect("/opt/homebrew/bin/adb", "127.0.0.1:5555", timeout=2.0)
        )
        # 不等 CMD_RESULT，直接把 AsyncCallInfo 投进去（此刻 _pending_async 还是空的）。
        ipc.event_queue.put(
            {
                "type": "CALLBACK",
                "ts": time.time(),
                "payload": {"msg": int(Message.AsyncCallInfo), "details": sample},
            }
        )
        await _wait_until(
            lambda: sample["async_call_id"] in client._early_async, "异步结果进入缓存"
        )
        assert await asyncio.wait_for(task, WAIT_TIMEOUT) is True
        assert client._early_async == {}
        assert client._pending_async == {}

    asyncio.run(scenario())


def test_close_cancels_pending_and_stops_consumer(ipc: _Harness) -> None:
    """``close()``：停消费线程、取消未决 Future 并注销长命令登记。"""
    client = ipc.make_client()
    supervisor = ipc.supervisor
    ipc.config["drop"].add("GET_UUID")

    async def scenario() -> None:
        client.start_consumer()
        thread = client._thread
        task = asyncio.create_task(client._send("GET_UUID", {}, timeout=WAIT_TIMEOUT))
        await _wait_until(lambda: bool(client._pending_cmds), "命令登记待决表")
        client.close()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._pending_cmds == {}
        assert thread is not None and not thread.is_alive()
        assert supervisor.released  # 取消未决命令时同样 release_command

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# screencap / get_image 的两级 Future 复用
# ---------------------------------------------------------------------------


def test_screencap_uses_resolution_suffix_and_two_level_future(
    ipc: _Harness, tmp_path: Path
) -> None:
    """SCREENCAP 复用同一套两级 Future；缺省落盘名按是否已知分辨率选后缀。"""
    client = ipc.make_client(screencap_dir=tmp_path / "shots")
    supervisor = ipc.supervisor
    ipc.config["async_call_id"] = 1

    async def scenario() -> None:
        client.start_consumer()
        ipc.event_queue.put(
            {
                "type": "CALLBACK",
                "ts": time.time(),
                "payload": {
                    "msg": int(Message.ConnectionInfo),
                    "details": {
                        "what": "ResolutionGot",
                        "details": {"width": 2560, "height": 1440},
                        "uuid": "f7c1c4ced5e96a23",
                    },
                },
            }
        )
        await _wait_until(
            lambda: client._last_resolution == (2560, 1440), "ResolutionGot 回调"
        )
        task = asyncio.create_task(client.screencap())
        await _wait_until(lambda: 1 in client._pending_async, "SCREENCAP 受理结果")
        ipc.event_queue.put(
            {
                "type": "CALLBACK",
                "ts": time.time(),
                "payload": {
                    "msg": int(Message.AsyncCallInfo),
                    "details": {
                        "async_call_id": 1,
                        "details": {"cost": 1, "ret": True},
                        "uuid": "f7c1c4ced5e96a23",
                        "what": "Screencap",
                    },
                },
            }
        )
        path = await asyncio.wait_for(task, WAIT_TIMEOUT)
        assert isinstance(path, Path)
        assert path.suffix == ".jpg"
        assert path.parent == tmp_path / "shots"
        assert path.parent.is_dir()
        data = await client.get_image(save_to=tmp_path / "explicit.jpg", bgr=False)
        assert data["path"] == str(tmp_path / "explicit.jpg")
        assert data["encoding"] == "raw"

    asyncio.run(scenario())

    get_images = [command for command in supervisor.tracked if command["type"] == "GET_IMAGE"]
    assert get_images, "SCREENCAP 之后必须发 GET_IMAGE"
    assert get_images[0]["payload"]["save_to"].endswith(".jpg")
    assert get_images[0]["payload"]["bgr"] is False
