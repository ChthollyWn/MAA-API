"""FakeAsst —— 按脚本产生回调事件的纯 Python 替身（docs/03 §8 测试策略）。

它让执行层、日志汇聚、重试与超时逻辑可以在没有 MaaCore、没有真机的环境下
完成测试，同时是 M1 崩溃注入与 IPC 契约测试的基座。

硬约束：

- 纯 Python：不引入任何 FFI 模块，不加载真实内核（不触碰 Asst 的 load 接口）；
- 回调形态与真实内核一致：``callback(message: int, details: bytes, arg)``，
  ``details`` 是 UTF-8 编码的 JSON，对齐 ``Asst.CallBackType`` 的 ``c_char_p``；
- 事件在后台线程里按剧本顺序发出，因此调用方可以观察到：
  “任务成功”（完成 + 全部完成）、“任务失败”（TaskChainError）、
  “任务卡死”（只发出前缀事件、``running()`` 恒为 True、脚本线程已结束）、
  “连接断开”（``ConnectionInfo:Disconnect`` 后 ``connected()`` 变 False）。

名词：

- ``FakeScript``：一次运行的剧本（事件序列 + 连接结果 + 是否卡死）；
- ``CallbackRecord``：已经回调出去的一条消息，测试可直接断言；
- ``FakeAsst.records``：全部回调记录；``FakeAsst.calls``：实例方法调用流水。
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Sequence

from maa_api.model.util.utils import InstanceOptionType, JSON, Message, StaticOptionType

__all__ = [
    "CallbackFn",
    "CallbackRecord",
    "FakeAsst",
    "FakeScript",
    "ScriptedEvent",
    "SCRIPTS",
    "DISCONNECT_SCRIPT",
    "FAILURE_SCRIPT",
    "STUCK_SCRIPT",
    "SUCCESS_SCRIPT",
    "get_script",
]

CallbackFn = Callable[[int, bytes, Any], None]
"""回调签名，与 ``Asst.CallBackType`` 对齐（message, details, arg）。"""

TaskId = int

_TASK_CHAIN_MESSAGES = frozenset({
    Message.TaskChainError,
    Message.TaskChainStart,
    Message.TaskChainCompleted,
    Message.TaskChainExtraInfo,
    Message.TaskChainStopped,
})


@dataclass(frozen=True)
class CallbackRecord:
    """替身回调出去的一条消息。"""

    message: Message
    details: dict[str, Any]
    raw_message: int
    raw_details: bytes
    arg: Any = None
    at: float = 0.0


@dataclass(frozen=True)
class ScriptedEvent:
    """剧本中的一步：延时 ``delay`` 秒后回调 ``message``。"""

    message: Message
    details: dict[str, Any] = field(default_factory=dict)
    delay: float = 0.0
    connected: bool | None = None
    """发出该事件后强制设置的连接状态；None 表示不改。"""

    running: bool | None = None
    """发出该事件后强制设置的运行状态；None 表示不改。"""


@dataclass(frozen=True)
class FakeScript:
    """一次运行的剧本。"""

    name: str
    events: tuple[ScriptedEvent, ...]
    description: str = ""
    connect_results: tuple[bool, ...] = (True,)
    """``connect`` 的返回值序列，用完后重复最后一个（默认一直成功）。"""

    append_task_fails: bool = False
    """True 时 ``append_task`` 返回 0（内核失败语义），不分配 task id。"""

    stuck: bool = False
    """True 表示剧本放完后 ``running()`` 仍保持 True（任务卡死）。"""


SUCCESS_SCRIPT = FakeScript(
    name="success",
    description="任务成功：TaskChainStart → TaskChainCompleted → AllTasksCompleted",
    events=(
        ScriptedEvent(Message.TaskChainStart),
        ScriptedEvent(Message.TaskChainCompleted),
        ScriptedEvent(Message.AllTasksCompleted),
    ),
)

FAILURE_SCRIPT = FakeScript(
    name="failure",
    description="任务失败：TaskChainStart → TaskChainError，没有完成事件",
    events=(
        ScriptedEvent(Message.TaskChainStart),
        ScriptedEvent(Message.TaskChainError, {"why": "scripted failure"}),
    ),
)

STUCK_SCRIPT = FakeScript(
    name="stuck",
    description="任务卡死：只发出 TaskChainStart，running() 保持 True",
    events=(ScriptedEvent(Message.TaskChainStart),),
    stuck=True,
)

DISCONNECT_SCRIPT = FakeScript(
    name="disconnect",
    description="连接断开：连接成功 → 任务开始 → ConnectionInfo(Disconnect)，连接与运行同时置假",
    events=(
        ScriptedEvent(Message.ConnectionInfo, {"what": "Connected"}, connected=True),
        ScriptedEvent(Message.TaskChainStart),
        ScriptedEvent(Message.ConnectionInfo, {"what": "Disconnect", "details": {}},
                      connected=False, running=False),
    ),
)

SCRIPTS: dict[str, FakeScript] = {
    script.name: script
    for script in (SUCCESS_SCRIPT, FAILURE_SCRIPT, STUCK_SCRIPT, DISCONNECT_SCRIPT)
}
"""内置剧本：success / failure / stuck / disconnect。"""


def get_script(script: FakeScript | str) -> FakeScript:
    """按名字取内置剧本，或原样返回传入的剧本。"""
    if isinstance(script, FakeScript):
        return script
    try:
        return SCRIPTS[script]
    except KeyError:
        raise KeyError(f"未知剧本 {script!r}，可用：{sorted(SCRIPTS)}") from None


class FakeAsst:
    """``AsstProtocol`` 的结构化实现（鸭子类型，不继承 Protocol）。

    :param script: 内置剧本名或 ``FakeScript``
    :param callback: 回调函数，签名 ``(message: int, details: bytes, arg)``
    :param arg: 回调时原样回传的自定义参数
    :param delay_scale: 剧本中每个 delay 的缩放系数，便于测试调快调慢
    :param screenshot: ``get_image`` / ``get_image_bgr`` 返回的假截图字节
    """

    # ---- 类级（静态接口）状态，测试可用 reset_class_state() 复位 ----
    resource_load_result: ClassVar[bool] = True
    resource_load_calls: ClassVar[list[tuple[Any, Any, Any]]] = []
    static_option_calls: ClassVar[list[tuple[Any, Any]]] = []
    connection_extras_calls: ClassVar[list[tuple[Any, Any]]] = []
    log_calls: ClassVar[list[tuple[Any, Any]]] = []

    def __init__(
        self,
        script: FakeScript | str = "success",
        callback: CallbackFn | None = None,
        arg: Any = None,
        *,
        delay_scale: float = 1.0,
        screenshot: bytes | None = None,
    ) -> None:
        self.script = get_script(script)
        self.callback = callback
        self.arg = arg
        self.delay_scale = delay_scale
        self.screenshot = screenshot

        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._script_finished = False
        self._stop_requested = False
        self._stopped_event_emitted = False
        self._running = False
        self._connected = False
        self._connect_count = 0

        self._records: list[CallbackRecord] = []
        self._callback_errors: list[BaseException] = []
        self._calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._task_ids = itertools.count(1)
        self._async_ids = itertools.count(1)
        self._tasks: dict[int, tuple[str, JSON]] = {}
        self._task_order: list[int] = []
        self._current_task: tuple[int, str] | None = None

        self._uuid = "fake-uuid-0001"
        self._version = "FakeAsst/0.1"

    # ------------------------------------------------------------------
    # 类级复位
    # ------------------------------------------------------------------

    @classmethod
    def reset_class_state(cls) -> None:
        """复位静态接口的结果与调用记录，避免用例之间互相污染。"""
        cls.resource_load_result = True
        cls.resource_load_calls.clear()
        cls.static_option_calls.clear()
        cls.connection_extras_calls.clear()
        cls.log_calls.clear()

    # ------------------------------------------------------------------
    # 静态接口
    # ------------------------------------------------------------------

    @staticmethod
    def load(path, incremental_path=None, user_dir=None) -> bool:
        FakeAsst.resource_load_calls.append((path, incremental_path, user_dir))
        return FakeAsst.resource_load_result

    @staticmethod
    def set_static_option(option_type: StaticOptionType, option_value: str) -> bool:
        FakeAsst.static_option_calls.append((option_type, option_value))
        return True

    @staticmethod
    def set_connection_extras(name: str, extras: JSON) -> None:
        FakeAsst.connection_extras_calls.append((name, extras))

    @staticmethod
    def log(level: str, message: str) -> None:
        FakeAsst.log_calls.append((level, message))

    @staticmethod
    def get_null_size() -> int:
        return 0

    # ------------------------------------------------------------------
    # 实例接口
    # ------------------------------------------------------------------

    def set_instance_option(self, option_type: InstanceOptionType, option_value: str) -> bool:
        self._record_call("set_instance_option", option_type, option_value)
        return True

    def connect(self, adb_path: str, address: str, config: str = "General") -> bool:
        self._record_call("connect", adb_path, address, config)
        return self._connect_from_script()

    def connect_async(self, adb_path: str, address: str,
                      config: str = "General", block: bool = True) -> int:
        self._record_call("connect_async", adb_path, address, config, block)
        call_id = next(self._async_ids)
        ok = self._connect_from_script()
        # 真实内核的异步结果通过回调送达，不看返回值
        self._emit(Message.AsyncCallInfo,
                   {"async_call_id": call_id, "what": "Connect", "ret": ok})
        return call_id

    def connected(self) -> bool:
        self._record_call("connected")
        return self._connected

    def back_to_home(self) -> bool:
        self._record_call("back_to_home")
        return True

    def click(self, x: int, y: int, block: bool = True) -> int:
        self._record_call("click", x, y, block)
        call_id = next(self._async_ids)
        self._emit(Message.AsyncCallInfo,
                   {"async_call_id": call_id, "what": "Click", "ret": True})
        return call_id

    def screencap(self, block: bool = True) -> int:
        self._record_call("screencap", block)
        call_id = next(self._async_ids)
        self._emit(Message.AsyncCallInfo,
                   {"async_call_id": call_id, "what": "Screencap", "ret": True})
        return call_id

    def get_image(self, size: int) -> bytes | None:
        self._record_call("get_image", size)
        return None if self.screenshot is None else self.screenshot[:size]

    def get_image_bgr(self, size: int) -> bytes | None:
        self._record_call("get_image_bgr", size)
        return None if self.screenshot is None else self.screenshot[:size]

    def get_uuid(self) -> str | None:
        self._record_call("get_uuid")
        return self._uuid if self._connected else None

    def get_tasks_list(self) -> list[int]:
        self._record_call("get_tasks_list")
        with self._lock:
            return list(self._task_order)

    def append_task(self, type_name: str, params: JSON = {}) -> TaskId:
        self._record_call("append_task", type_name, params)
        if self.script.append_task_fails:
            return 0
        task_id = next(self._task_ids)
        with self._lock:
            self._tasks[task_id] = (type_name, params)
            self._task_order.append(task_id)
            self._current_task = (task_id, type_name)
        return task_id

    def set_task_params(self, task_id: TaskId, params: JSON) -> bool:
        self._record_call("set_task_params", task_id, params)
        with self._lock:
            if task_id not in self._tasks:
                return False
            self._tasks[task_id] = (self._tasks[task_id][0], params)
            return True

    def start(self) -> bool:
        self._record_call("start")
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop_requested = False
            self._script_finished = False
            self._thread = threading.Thread(
                target=self._run_script, name="FakeAsst-script", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> bool:
        self._record_call("stop")
        with self._lock:
            was_running = self._running
            self._stop_requested = True
            self._running = False
            task = self._current_task
            self._tasks.clear()
            self._task_order.clear()
            self._current_task = None
        if was_running and task is not None and not self._stopped_event_emitted:
            self._stopped_event_emitted = True
            self._emit(Message.TaskChainStopped,
                       {"taskid": task[0], "taskchain": task[1]})
        return True

    def running(self) -> bool:
        self._record_call("running")
        return self._running

    def get_version(self) -> str:
        self._record_call("get_version")
        return self._version

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """按名字调用替身上的公开方法（worker 命令循环的通用分发入口）。"""
        if method.startswith("_"):
            raise AttributeError(f"{method!r} 不是可调用的公开方法")
        self._record_call("call", method, *args, **kwargs)
        func = getattr(self, method, None)
        if not callable(func):
            raise AttributeError(f"FakeAsst 没有方法 {method!r}")
        return func(*args, **kwargs)

    # ------------------------------------------------------------------
    # 观测接口
    # ------------------------------------------------------------------

    def set_callback(self, callback: CallbackFn | None, arg: Any = None) -> None:
        """替换回调函数（模拟 worker 在启动后才注册回调）。"""
        with self._lock:
            self.callback = callback
            self.arg = arg

    @property
    def records(self) -> list[CallbackRecord]:
        """已回调的全部消息（副本）。"""
        with self._lock:
            return list(self._records)

    @property
    def callback_errors(self) -> list[BaseException]:
        """回调函数抛出的异常，便于定位测试自身的 bug。"""
        with self._lock:
            return list(self._callback_errors)

    @property
    def calls(self) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        """实例方法的调用流水 ``(name, args, kwargs)``（副本）。"""
        with self._lock:
            return list(self._calls)

    def messages(self) -> list[Message]:
        """已回调消息的类型序列。"""
        return [record.message for record in self.records]

    def records_of(self, message: Message) -> list[CallbackRecord]:
        """某类消息的全部回调记录。"""
        return [record for record in self.records if record.message == message]

    def calls_of(self, name: str) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        """某个方法的调用流水。"""
        return [call for call in self.calls if call[0] == name]

    def script_finished(self) -> bool:
        """剧本线程是否已经放完事件（卡死剧本放完后 ``running()`` 仍为 True）。"""
        with self._lock:
            return self._script_finished

    def stuck_observed(self) -> bool:
        """是否处于“剧本已放完、线程已退出，但 running() 仍为 True”的卡死态。"""
        return self.script.stuck and self.script_finished() and self.running()

    def wait_for(self, predicate: Callable[["FakeAsst"], bool],
                 timeout: float = 2.0, interval: float = 0.005) -> bool:
        """轮询等待条件成立，超时返回 False（不抛异常）。"""
        deadline = time.monotonic() + timeout
        while True:
            if predicate(self):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    def wait_for_record(self, message: Message, timeout: float = 2.0,
                        count: int = 1) -> bool:
        """等待某类消息累计出现 count 条，超时返回 False。"""
        return self.wait_for(lambda fake: len(fake.records_of(message)) >= count,
                             timeout=timeout)

    def wait_until_script_done(self, timeout: float = 2.0) -> bool:
        """等待剧本线程放完；卡死剧本也会在放完前缀后返回 True。"""
        if self._thread is not None:
            self._thread.join(timeout)
        return self.script_finished()

    def join(self, timeout: float | None = None) -> bool:
        """join 剧本线程，线程已结束时返回 True。"""
        if self._thread is not None:
            self._thread.join(timeout)
        return self._thread is not None and not self._thread.is_alive()

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _record_call(self, name: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self._calls.append((name, args, kwargs))

    def _connect_from_script(self) -> bool:
        with self._lock:
            results: Sequence[bool] = self.script.connect_results or (True,)
            ok = bool(results[min(self._connect_count, len(results) - 1)])
            self._connect_count += 1
            self._connected = ok
            return ok

    def _run_script(self) -> None:
        try:
            for step in self.script.events:
                if not self._sleep(step.delay * self.delay_scale):
                    return
                self._emit(step.message, step.details,
                           connected=step.connected, running=step.running)
            if not self.script.stuck:
                with self._lock:
                    if not self._stop_requested:
                        self._running = False
        finally:
            with self._lock:
                self._script_finished = True

    def _sleep(self, delay: float) -> bool:
        """可被打断的等待；返回 False 表示收到 stop 请求。"""
        deadline = time.monotonic() + max(delay, 0.0)
        while True:
            if self._stop_requested:
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(0.01, remaining))

    def _emit(self, message: Message, details: dict[str, Any] | None = None,
              *, connected: bool | None = None,
              running: bool | None = None) -> CallbackRecord:
        with self._lock:
            payload = dict(details or {})
            if message in _TASK_CHAIN_MESSAGES and self._current_task is not None:
                payload.setdefault("taskid", self._current_task[0])
                payload.setdefault("taskchain", self._current_task[1])
            raw_details = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            record = CallbackRecord(
                message=message,
                details=payload,
                raw_message=int(message.value),
                raw_details=raw_details,
                arg=self.arg,
                at=time.monotonic(),
            )
            self._records.append(record)
            if connected is not None:
                self._connected = connected
            if running is not None:
                self._running = running
            callback = self.callback
        if callback is not None:
            try:
                callback(record.raw_message, record.raw_details, self.arg)
            except BaseException as exc:  # noqa: BLE001 - 记录而非吞掉，便于定位
                with self._lock:
                    self._callback_errors.append(exc)
        return record
