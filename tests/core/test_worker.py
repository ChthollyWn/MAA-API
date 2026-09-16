"""M1-07 ``core/worker.py`` 的端到端测试：真实 spawn 子进程 + 真实 Queue。

这里不加载 MaaCore、不连设备：``boot_config["asst_factory"]`` 指向
``tests.fakes.fake_asst:FakeAsst``（纯 Python 替身，毫秒级完成），因此整套用例可以在
任何机器上跑。被测的却是真正的进程边界 —— ``multiprocessing.get_context("spawn")``
启动真实子进程、两条真实 ``ctx.Queue()`` 传消息，覆盖：

1. 启动序列：``READY`` 载荷（version / pid）、``GET_VERSION``、``PING``/``PONG``
   不进内核、``SHUTDOWN`` 后子进程以退出码 0 自然结束；
2. 回调桥接：``APPEND_TASK`` + ``START`` 后替身剧本的调用跨进程变成 ``CALLBACK``
   事件，``msg`` 是 ``maa_api.core.enums.Message`` 的整数值；
3. ``LOAD_RESOURCE`` 的 ``loaded`` / ``skipped`` 顺序与「跳过不存在的层、不跳过
   失败的层」语义；
4. ``GET_IMAGE`` 落盘的两条路径（已知分辨率 → JPEG；分辨率未知 → 原样 raw 写盘）；
5. 启动失败与命令失败的上报形态（``FATAL`` / ``CMD_RESULT{ok: False}``）；
6. 日志桥接（子进程 logging → ``LOG`` 事件）与回调桥接的指针 arg 路径
   （真实 ``Asst`` 注册时内核回传的是整数指针；替身直接传队列对象，走回退路径）。

所有等待都有超时（≤5 秒），失败信息里带上「已收到的事件列表」，避免挂死或只看到
一句 ``assert None is not None``。子进程在 fixture 收尾时统一 kill + 关闭队列；父进程
侧关闭队列用 ``close() + cancel_join_thread()``，子进程侧绝不 cancel（否则消息可能
丢在 feeder 线程里，M1-02 实测）。

本模块里的顶层类会被 spawn 子进程通过 ``asst_factory="tests.core.test_worker:..."``
重新 import，因此模块顶层不能有副作用（测试函数/fixture 体内的才安全）。
"""

from __future__ import annotations

import ctypes
import io
import multiprocessing
import queue as queue_module
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import pytest
from PIL import Image

from maa_api.core import worker as worker_module
from maa_api.core.enums import InstanceOptionKey, Message
from maa_api.core.protocol import make_command
from maa_api.core.worker import core_worker_main
from maa_api.domain.errors import ErrorCode
from tests.fakes.fake_asst import FakeAsst

WORKER_TIMEOUT = 5.0
EVENT_POLL_INTERVAL = 0.005
FAKE_FACTORY = "tests.fakes.fake_asst:FakeAsst"


# ----------------------------------------------------------------------
# 子进程内使用的替身（spawn 按限定名重新 import 本模块）
# ----------------------------------------------------------------------


class _BaseLoadFailingAsst(FakeAsst):
    """基础资源 ``load`` 恒失败：启动序列必须转成 ``FATAL`` 而不是裸崩。"""

    @staticmethod
    def load(path, incremental=False, user_dir=None) -> bool:
        return False


class _IncrementalLoadFailingAsst(FakeAsst):
    """基础资源成功、增量层失败：不存在的层要跳过，失败的层必须中断上报。"""

    @staticmethod
    def load(path, incremental=False, user_dir=None) -> bool:
        return not incremental


class _OptionRecordingAsst(FakeAsst):
    """把实例选项编码进 ``get_version``，让父进程能跨进程断言选项确实被应用。"""

    def set_instance_option(self, option_type, option_value) -> bool:
        self._version = f"option:{int(option_type)}={option_value}"
        return True


# ----------------------------------------------------------------------
# 事件收集器与 worker 夹具
# ----------------------------------------------------------------------


class _EventLog:
    """后台线程持续清空 ``event_queue``，供用例做带超时的等待与失败诊断。"""

    def __init__(self, event_queue: Any) -> None:
        self._queue = event_queue
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._thread = threading.Thread(
            target=self._read, name="test-worker-events", daemon=True
        )
        self._thread.start()

    def _read(self) -> None:
        while not self._closed.is_set():
            try:
                event = self._queue.get(timeout=0.05)
            except queue_module.Empty:
                continue
            except (EOFError, OSError, ValueError):  # 队列已关闭
                return
            with self._lock:
                self._events.append(event)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def types(self) -> list[str]:
        return [event.get("type") for event in self.snapshot()]

    def wait(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float = WORKER_TIMEOUT
    ) -> Optional[dict[str, Any]]:
        """轮询等待首个满足条件的事件，超时返回 None（不抛异常）。"""
        deadline = time.monotonic() + timeout
        while True:
            for event in self.snapshot():
                if predicate(event):
                    return event
            if time.monotonic() >= deadline:
                return None
            time.sleep(EVENT_POLL_INTERVAL)

    def wait_type(
        self, type_name: str, timeout: float = WORKER_TIMEOUT
    ) -> Optional[dict[str, Any]]:
        return self.wait(lambda event: event.get("type") == type_name, timeout)

    def wait_cmd_result(
        self, cmd_id: Any, timeout: float = WORKER_TIMEOUT
    ) -> Optional[dict[str, Any]]:
        return self.wait(
            lambda event: event.get("type") == "CMD_RESULT"
            and event.get("payload", {}).get("cmd_id") == cmd_id,
            timeout,
        )

    def close(self) -> None:
        self._closed.set()
        self._thread.join(timeout=2.0)


def _boot_config(**overrides: Any) -> dict[str, Any]:
    """M1-07 契约的 boot_config：纯 dict、可 pickle，不依赖真实内核。"""
    config: dict[str, Any] = {
        "maa_path": "resource/lib/maa",
        "user_dir": None,
        "incremental_paths": [],
        "instance_options": {},
        "asst_factory": FAKE_FACTORY,
        "asst_factory_kwargs": {"script": "success"},
    }
    config.update(overrides)
    return config


class _WorkerHarness:
    """一次 worker 子进程 + 两条真实 Queue + 事件收集器。"""

    def __init__(self, ctx: Any, boot_config: dict[str, Any]) -> None:
        self.cmd_queue = ctx.Queue()
        self.event_queue = ctx.Queue()
        self.events = _EventLog(self.event_queue)
        self.process = ctx.Process(
            target=core_worker_main,
            args=(self.cmd_queue, self.event_queue, boot_config),
            name="test-core-worker",
        )
        self.process.start()

    # --- 发命令 ---

    def send(self, command_type: str, **payload: Any) -> dict[str, Any]:
        """按 docs/02 §3.1 的消息形态发一条命令（借协议层构造，顺带校验必填键）。

        首参刻意不叫 ``type_name``：``APPEND_TASK`` 的 payload 自身就有
        ``type_name`` 键，同名会让 ``send("APPEND_TASK", type_name=...)`` 撞车。
        """
        command = make_command(command_type, **payload)
        self.cmd_queue.put(command)
        return command

    def call(
        self, command_type: str, timeout: float = WORKER_TIMEOUT, **payload: Any
    ) -> dict[str, Any]:
        """发命令并等待同 ``cmd_id`` 的 ``CMD_RESULT``，返回其 payload。"""
        command = self.send(command_type, **payload)
        result = self.events.wait_cmd_result(command["cmd_id"], timeout)
        assert result is not None, self.describe(
            f"{command_type} 未在 {timeout}s 内返回 CMD_RESULT"
        )
        return result["payload"]

    # --- 观测 ---

    def wait_ready(self, timeout: float = WORKER_TIMEOUT) -> dict[str, Any]:
        event = self.events.wait_type("READY", timeout)
        assert event is not None, self.describe(f"未在 {timeout}s 内收到 READY")
        return event

    def describe(self, message: str) -> str:
        """失败诊断：事件类型序列 + 完整事件，避免只看到一句断言。"""
        return f"{message}；已收到事件：{self.events.snapshot()}"

    def wait_exit(self, timeout: float = WORKER_TIMEOUT) -> Optional[int]:
        self.process.join(timeout=timeout)
        return self.process.exitcode

    # --- 收尾 ---

    def shutdown(self, graceful: bool = True, timeout: float = WORKER_TIMEOUT) -> Optional[int]:
        self.send("SHUTDOWN", graceful=graceful)
        return self.wait_exit(timeout)

    def close(self) -> None:
        if self.process.is_alive():
            self.process.kill()
        self.process.join(timeout=5.0)
        self.events.close()
        for queue in (self.cmd_queue, self.event_queue):
            queue.close()
            queue.cancel_join_thread()


@pytest.fixture(scope="module")
def spawn_ctx():
    """spawn 上下文：与 docs/13 ADR-03 的生产启动方式一致（最严的 pickle 考验）。"""
    return multiprocessing.get_context("spawn")


@pytest.fixture
def worker_factory(spawn_ctx) -> Iterator[Callable[..., _WorkerHarness]]:
    created: list[_WorkerHarness] = []

    def start(**overrides: Any) -> _WorkerHarness:
        harness = _WorkerHarness(spawn_ctx, _boot_config(**overrides))
        created.append(harness)
        return harness

    yield start

    for harness in created:
        harness.close()


def _png_bytes(width: int = 4, height: int = 4) -> bytes:
    """真实内核 ``AsstGetImage`` 返回的是 PNG 编码字节（M1-04 真机实测）。"""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# ----------------------------------------------------------------------
# ① 启动序列、PING/PONG 与 SHUTDOWN
# ----------------------------------------------------------------------


def test_ready_version_ping_pong_and_shutdown(worker_factory) -> None:
    worker = worker_factory()
    ready = worker.wait_ready()

    assert set(ready["payload"]) == {"version", "pid"}
    assert ready["payload"]["version"] == "FakeAsst/0.1"
    assert ready["payload"]["pid"] == worker.process.pid

    version = worker.call("GET_VERSION")
    assert version["ok"] is True
    assert version["data"] == "FakeAsst/0.1"
    assert version["error"] is None
    assert version["cmd_id"]

    ping = worker.send("PING", seq=7)
    pong = worker.events.wait_type("PONG")
    assert pong is not None, worker.describe("未收到 PONG")
    assert pong["payload"] == {"seq": 7}
    # PING 不进 asst：不该有为它生成的 CMD_RESULT（心跳只证明命令循环活着）。
    assert worker.events.wait_cmd_result(ping["cmd_id"], timeout=0.3) is None

    assert worker.shutdown(graceful=True) == 0, worker.describe("worker 未正常退出")


# ----------------------------------------------------------------------
# ② 回调桥接：CALLBACK 事件保持原始 msg / details
# ----------------------------------------------------------------------


def test_append_task_and_start_forward_raw_callbacks(worker_factory) -> None:
    worker = worker_factory()
    worker.wait_ready()

    appended = worker.call(
        "APPEND_TASK", type_name="StartUp", params={"client_type": "Official"}
    )
    assert appended["ok"] is True, worker.describe("APPEND_TASK 失败")
    assert appended["data"] == 1

    started = worker.call("START")
    assert started["ok"] is True and started["data"] is True

    def callback_of(message: Message) -> Callable[[dict[str, Any]], bool]:
        return lambda event: (
            event.get("type") == "CALLBACK"
            and event["payload"]["msg"] == int(message)
        )

    chain_start = worker.events.wait(callback_of(Message.TaskChainStart))
    assert chain_start is not None, worker.describe("未收到 TaskChainStart 回调")
    assert isinstance(chain_start["payload"]["msg"], int)
    assert chain_start["payload"]["details"]["taskchain"] == "StartUp"

    completed = worker.events.wait(callback_of(Message.TaskChainCompleted))
    assert completed is not None, worker.describe("未收到 TaskChainCompleted 回调")

    all_done = worker.events.wait(callback_of(Message.AllTasksCompleted))
    assert all_done is not None, worker.describe("未收到 AllTasksCompleted 回调")
    assert isinstance(all_done["payload"]["details"], dict)

    assert worker.shutdown() == 0


# ----------------------------------------------------------------------
# ③ LOAD_RESOURCE：loaded / skipped 顺序与跳过语义
# ----------------------------------------------------------------------


def test_load_resource_reports_loaded_and_skipped_in_order(worker_factory, tmp_path) -> None:
    base = tmp_path / "base"
    first = tmp_path / "layer-a"
    second = tmp_path / "layer-b"
    missing = tmp_path / "layer-missing"
    (first / "resource").mkdir(parents=True)
    (second / "resource").mkdir(parents=True)

    worker = worker_factory()
    worker.wait_ready()

    result = worker.call(
        "LOAD_RESOURCE",
        path=str(base),
        incremental_paths=[str(first), str(missing), str(second)],
    )
    assert result["ok"] is True, worker.describe("LOAD_RESOURCE 失败")
    assert result["data"] == {
        "loaded": [str(base), str(first), str(second)],
        "skipped": [str(missing)],
    }

    assert worker.shutdown() == 0


# ----------------------------------------------------------------------
# ④ 启动选项确实按 InstanceOptionKey 名字应用
# ----------------------------------------------------------------------


def test_boot_instance_options_are_applied(worker_factory) -> None:
    worker = worker_factory(
        asst_factory="tests.core.test_worker:_OptionRecordingAsst",
        instance_options={"ClientType": "Official"},
    )
    ready = worker.wait_ready()
    assert ready["payload"]["version"] == f"option:{int(InstanceOptionKey.ClientType)}=Official"
    assert worker.shutdown() == 0


# ----------------------------------------------------------------------
# ⑤ GET_IMAGE 落盘：已知分辨率 → JPEG
# ----------------------------------------------------------------------


def test_get_image_writes_jpeg_when_resolution_known(worker_factory, tmp_path) -> None:
    screenshot = bytes((index * 7) % 256 for index in range(4 * 4 * 3))
    save_to = tmp_path / "shots" / "shot.jpg"
    worker = worker_factory(
        asst_factory_kwargs={
            "script": "success",
            "screenshot": screenshot,
            "resolution": (4, 4),
        }
    )
    worker.wait_ready()

    result = worker.call(
        "GET_IMAGE", bgr=True, save_to=str(save_to), size=len(screenshot)
    )
    assert result["ok"] is True, worker.describe("GET_IMAGE 失败")

    data = result["data"]
    assert data["encoding"] == "jpeg"
    assert data["path"] == str(save_to)
    assert data["width"] == 4 and data["height"] == 4
    written = Path(data["path"])
    assert written.is_file()
    assert written.stat().st_size > 0
    assert written.stat().st_size == data["size"]
    assert written.read_bytes()[:2] == b"\xff\xd8"  # JPEG magic

    assert worker.shutdown() == 0


def test_get_image_reencodes_encoded_screenshot(worker_factory, tmp_path) -> None:
    """``bgr=False`` 时真实内核给的是 PNG：Pillow 解码后仍按契约落成 JPEG。"""
    screenshot = _png_bytes(4, 4)
    save_to = tmp_path / "shots" / "encoded.jpg"
    worker = worker_factory(
        asst_factory_kwargs={
            "script": "success",
            "screenshot": screenshot,
            "resolution": (4, 4),
        }
    )
    worker.wait_ready()

    result = worker.call(
        "GET_IMAGE", bgr=False, save_to=str(save_to), size=len(screenshot)
    )
    assert result["ok"] is True, worker.describe("GET_IMAGE 失败")
    assert result["data"]["encoding"] == "jpeg"
    assert result["data"]["width"] == 4 and result["data"]["height"] == 4
    assert Path(result["data"]["path"]).read_bytes()[:2] == b"\xff\xd8"

    assert worker.shutdown() == 0


# ----------------------------------------------------------------------
# ⑥ GET_IMAGE 落盘：分辨率未知 → 原样 raw
# ----------------------------------------------------------------------


def test_get_image_writes_raw_bytes_when_resolution_unknown(worker_factory, tmp_path) -> None:
    screenshot = bytes(range(3)) * 4
    save_to = tmp_path / "nested" / "shot.bin"
    worker = worker_factory(
        asst_factory_kwargs={
            "script": "success",
            "screenshot": screenshot,
            "resolution": None,
        }
    )
    worker.wait_ready()

    result = worker.call(
        "GET_IMAGE", bgr=False, save_to=str(save_to), size=len(screenshot)
    )
    assert result["ok"] is True, worker.describe("GET_IMAGE 失败")
    assert result["data"] == {
        "path": str(save_to) + ".rgb",
        "size": len(screenshot),
        "encoding": "raw",
    }
    assert Path(result["data"]["path"]).read_bytes() == screenshot

    assert worker.shutdown() == 0


# ----------------------------------------------------------------------
# ⑦ 命令失败 → CMD_RESULT{ok: False, code: CORE_COMMAND_FAILED}
# ----------------------------------------------------------------------


def test_failed_command_maps_to_core_command_failed(worker_factory) -> None:
    worker = worker_factory(
        asst_factory_kwargs={
            "script": "success",
            "screenshot": b"\x00" * (2 * 2 * 3),
            "resolution": (2, 2),
        }
    )
    worker.wait_ready()

    result = worker.call("GET_IMAGE", bgr=False, save_to="")
    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"]["code"] == ErrorCode.CORE_COMMAND_FAILED.value
    assert "save_to" in result["error"]["message"]

    # 单条命令失败不带崩 worker：后续命令照常执行。
    assert worker.call("GET_VERSION")["ok"] is True
    assert worker.shutdown() == 0


# ----------------------------------------------------------------------
# ⑧ 启动失败 → FATAL 且进程正常退出
# ----------------------------------------------------------------------


def test_startup_load_failure_emits_fatal_and_exits(worker_factory) -> None:
    worker = worker_factory(asst_factory="tests.core.test_worker:_BaseLoadFailingAsst")

    fatal = worker.events.wait_type("FATAL")
    assert fatal is not None, worker.describe("未收到 FATAL")
    assert "基础资源加载失败" in fatal["payload"]["error"]
    assert fatal["payload"]["traceback"]
    assert worker.events.wait_type("READY", timeout=0.3) is None

    assert worker.wait_exit() == 0, worker.describe("启动失败后 worker 未正常退出")
    assert not worker.process.is_alive()


def test_incremental_layer_failure_emits_fatal(worker_factory, tmp_path) -> None:
    layer = tmp_path / "layer-broken"
    (layer / "resource").mkdir(parents=True)

    worker = worker_factory(
        asst_factory="tests.core.test_worker:_IncrementalLoadFailingAsst",
        incremental_paths=[str(layer)],
    )
    fatal = worker.events.wait_type("FATAL")
    assert fatal is not None, worker.describe("未收到 FATAL")
    assert "增量资源层加载失败" in fatal["payload"]["error"]
    assert worker.wait_exit() == 0


# ----------------------------------------------------------------------
# ⑨ 日志桥接
# ----------------------------------------------------------------------


def test_log_bridge_forwards_child_logs(worker_factory) -> None:
    worker = worker_factory()
    worker.wait_ready()

    log_event = worker.events.wait(
        lambda event: event.get("type") == "LOG"
        and "worker 启动" in event["payload"]["content"]
    )
    assert log_event is not None, worker.describe("未收到 LOG 事件")
    assert set(log_event["payload"]) == {"level", "content"}
    assert log_event["payload"]["level"] == "INFO"

    assert worker.shutdown() == 0


# ----------------------------------------------------------------------
# ⑩ 回调桥接的指针路径（真实 Asst 的注册方式）
# ----------------------------------------------------------------------


class _EventSink:
    """只实现 ``put`` 的假队列，用于在父进程内直接驱动回调桥接。"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def put(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def test_callback_bridge_reads_queue_through_pointer_arg(monkeypatch) -> None:
    """真实 ``Asst`` 注册时 arg 是 ``c_void_p(id(event_queue))``：按指针解释。

    替身路径（直接把队列对象当 arg）由上面 ② 的跨进程用例覆盖；这里补上真实内核
    路径：内核回传的是整数指针，桥接必须 ``ctypes.cast(arg, ctypes.py_object)``
    取回队列。顺带钉住「回调线程内绝不抛异常」：坏 JSON、None、悬垂 arg 都只能被
    静默吞掉。
    """
    sink = _EventSink()
    monkeypatch.setattr(worker_module, "_EVENT_QUEUE", sink)

    details = '{"what":"ExceededLimit","中文":1}'.encode("utf-8")
    worker_module._callback_bridge(20003, details, id(sink))
    worker_module._callback_bridge(20003, details, ctypes.c_void_p(id(sink)))

    assert len(sink.events) == 2
    for event in sink.events:
        assert event["type"] == "CALLBACK"
        assert isinstance(event["ts"], float)
        assert event["payload"]["msg"] == 20003
        assert event["payload"]["details"] == {"what": "ExceededLimit", "中文": 1}

    # 坏 JSON：静默吞掉，不留半条事件（异常穿 C 边界是未定义行为）
    worker_module._callback_bridge(1, b"not json", id(sink))
    assert len(sink.events) == 2

    # details 为 NULL：按空 dict 处理，仍然不抛
    worker_module._callback_bridge(1, None, id(sink))
    assert sink.events[-1]["payload"] == {"msg": 1, "details": {}}

    # arg 不是指针（替身直接传队列对象）：回退到模块级 event_queue，同样不抛
    worker_module._callback_bridge(1, details, sink)
    assert sink.events[-1]["payload"]["msg"] == 1
    assert len(sink.events) == 4
