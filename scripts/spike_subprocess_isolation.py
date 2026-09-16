#!/usr/bin/env python3
"""M1-02 方案前置实测：子进程隔离 + multiprocessing.Queue IPC 最小验证。

这是 docs/12 §5「M1 的方案验证前置」要求的丢弃式探针：在投入 M1-07/M1-08 的完整
实现前，用最小成本验证 ADR-02（子进程隔离）与 ADR-03（Queue IPC）这条路线是否成立。
本脚本**不是生产代码**，不产出 `maa_api/core/worker.py` / `supervisor.py`。

一条链路走完 6 个检查点：

1. `spawn` 子进程（`multiprocessing.get_context("spawn")`，父进程持线程时 fork 易死锁）；
2. 子进程内加载真实 `libMaaCore.dylib` 与基础资源（不做版本检查、不下载、不连设备）；
3. 主进程通过 `cmd_queue` 下发 `CONNECT` / `APPEND_TASK` / `START`，子进程回报 `CMD_RESULT`；
4. MaaCore 回调在子进程内被桥接成 `CALLBACK` 事件，跨 `event_queue` 回到主进程；
5. 主进程 `os.kill(pid, SIGKILL)` 杀掉子进程，记录 exitcode（应为 -9）；
6. 再 spawn 第二个子进程并等到 `READY`，证明「被杀 → 重启」这条恢复路径成立。

设计约束（来自 docs/03 §3.1、§3.2）：

- worker 启动序列里**没有**版本检查与下载，因此本脚本的子进程启动序列也不碰网络；
- 回调桥接运行在 MaaCore 自己的回调线程上：只做 `json.loads` + `put`，**绝不抛异常**
  （异常穿过 C 调用边界是未定义行为）；
- 消息是纯 dict，只含可 pickle 的基础类型——`spawn` 下不可 pickle 的参数会在运行时才暴露，
  所以子进程只接收路径字符串、dict 与两个 `Queue`，不接收任何服务对象。

实测到的两个坑（详见 tests/fixtures/spike_subprocess_findings.md）：

- 内核 v6.17.5 会校验任务的必填参数：`APPEND_TASK("StartUp", {})` 返回 0（被拒），
  带 `{"client_type": "Official"}` 才返回非零 task id。故本脚本给 `--task-params` 一个
  「能跑通 StartUp」的默认值；
- MaaCore 的 AsstMsg 取值不是 1..12 连号：`ConnectionInfo = 2`、`AsyncCallInfo = 4`、
  `TaskChain* = 1000x`、`SubTask* = 2000x`。

产物：

- `tests/fixtures/spike_subprocess_result.json`：6 个 bool 检查点 + observations；
- `tests/fixtures/spike_subprocess_findings.md`：人读实测记录（由本次实测输出整理）。

用法::

    cd <仓库根>
    DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
        .venv/bin/python scripts/spike_subprocess_isolation.py

    # 只看参数、不加载内核：
    .venv/bin/python scripts/spike_subprocess_isolation.py --help

退出码：6 个检查点全为 true 时 0，否则 1（此时仍写出结果 JSON，不伪造成功）。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing
import os
import platform
import queue as queue_module
import signal
import sys
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ── 事件类型（docs/02 §3.2） ────────────────────────────────────────────────
EVENT_CALLBACK = "CALLBACK"
EVENT_READY = "READY"
EVENT_CMD_RESULT = "CMD_RESULT"
EVENT_LOG = "LOG"
EVENT_FATAL = "FATAL"

# ── 命令类型（docs/02 §3.1） ────────────────────────────────────────────────
CMD_CONNECT = "CONNECT"
CMD_APPEND_TASK = "APPEND_TASK"
CMD_START = "START"
CMD_PING = "PING"
CMD_SHUTDOWN = "SHUTDOWN"

# ── MaaCore AsstMsg 真实取值（实测 v6.17.5，等价于旧实现的 Message 枚举） ──
# 刻意不用 1..12 连号：TaskChain* 是 1000x，SubTask* 是 2000x。
MSG_INTERNAL_ERROR = 0
MSG_INIT_FAILED = 1
MSG_CONNECTION_INFO = 2
MSG_ALL_TASKS_COMPLETED = 3
MSG_ASYNC_CALL_INFO = 4
MSG_DESTROYED = 5
MSG_TASK_CHAIN_ERROR = 10000
MSG_TASK_CHAIN_START = 10001
MSG_TASK_CHAIN_COMPLETED = 10002
MSG_TASK_CHAIN_EXTRA_INFO = 10003
MSG_TASK_CHAIN_STOPPED = 10004
MSG_SUB_TASK_ERROR = 20000
MSG_SUB_TASK_START = 20001
MSG_SUB_TASK_COMPLETED = 20002
MSG_SUB_TASK_EXTRA_INFO = 20003
MSG_SUB_TASK_STOPPED = 20004

MSG_NAMES: dict[int, str] = {
    MSG_INTERNAL_ERROR: "InternalError",
    MSG_INIT_FAILED: "InitFailed",
    MSG_CONNECTION_INFO: "ConnectionInfo",
    MSG_ALL_TASKS_COMPLETED: "AllTasksCompleted",
    MSG_ASYNC_CALL_INFO: "AsyncCallInfo",
    MSG_DESTROYED: "Destroyed",
    MSG_TASK_CHAIN_ERROR: "TaskChainError",
    MSG_TASK_CHAIN_START: "TaskChainStart",
    MSG_TASK_CHAIN_COMPLETED: "TaskChainCompleted",
    MSG_TASK_CHAIN_EXTRA_INFO: "TaskChainExtraInfo",
    MSG_TASK_CHAIN_STOPPED: "TaskChainStopped",
    MSG_SUB_TASK_ERROR: "SubTaskError",
    MSG_SUB_TASK_START: "SubTaskStart",
    MSG_SUB_TASK_COMPLETED: "SubTaskCompleted",
    MSG_SUB_TASK_EXTRA_INFO: "SubTaskExtraInfo",
    MSG_SUB_TASK_STOPPED: "SubTaskStopped",
}

LIB_FILENAMES = {
    "Darwin": "libMaaCore.dylib",
    "Linux": "libMaaCore.so",
    "Windows": "MaaCore.dll",
}

ENV_VAR_NAMES = {
    "Darwin": "DYLD_LIBRARY_PATH",
    "Linux": "LD_LIBRARY_PATH",
    "Windows": "PATH",
}

# 子进程模块级全局：回调桥接没有别的入口拿 event_queue（arg 指针方案依赖 GC 契约，
# 见 docs/03 §3.2；改用模块级全局更直白）。模块级持有同时保证 Queue 不被回收。
_EVENT_QUEUE: Any = None
# 回调计数：用于向主进程证明「子进程 put 了几条 = 主进程收到几条」，一条都没丢。
_CALLBACK_ENQUEUED = 0
_CALLBACK_PUT_FAILED = 0


# ── MaaCore 最小 FFI 封装 ───────────────────────────────────────────────────


class Asst:
    """只绑本卡用得到的 9 个 C API 的丢弃式封装。

    刻意不 import ``maa_api.model.core.asst.Asst``：那份旧实现待 M1-04 重写，且其
    import 链会 import ``maa_api.config.config``（import 期 mkdir + 拷贝配置文件），
    会给「子进程启动耗时」的测量引入无关变量。签名与旧实现保持一致，便于对照。
    """

    CallBackType = ctypes.CFUNCTYPE(
        None, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p
    )

    _lib: Any = None

    @classmethod
    def load(cls, path: Any, user_dir: Any = None) -> bool:
        """dlopen + ``AsstSetUserDir`` + ``AsstLoadResource``；不做网络 IO。"""
        maa_path = Path(path)
        filename = LIB_FILENAMES.get(platform.system(), "libMaaCore.dylib")
        lib_path = maa_path / filename
        if not lib_path.is_file():
            raise FileNotFoundError(f"{maa_path} 下找不到 {filename}")

        env_var = ENV_VAR_NAMES.get(platform.system())
        if env_var:
            existing = os.environ.get(env_var, "")
            if str(maa_path) not in existing.split(os.pathsep):
                os.environ[env_var] = (
                    f"{maa_path}{os.pathsep}{existing}" if existing else str(maa_path)
                )

        lib = ctypes.CDLL(str(lib_path))
        _bind_lib(lib)
        cls._lib = lib

        ok = True
        if user_dir:
            # 注意：user_dir 必须已存在，否则 AsstSetUserDir 返回 False。
            ok = bool(lib.AsstSetUserDir(str(user_dir).encode("utf-8"))) and ok
        ok = bool(lib.AsstLoadResource(str(maa_path).encode("utf-8"))) and ok
        return ok

    def __init__(self, callback: Any = None, arg: Any = None) -> None:
        # 必须持有强引用：CFUNCTYPE 对象被 GC 后内核回调会落到已释放的 trampoline 上。
        self._callback = callback
        c_arg = ctypes.c_void_p(id(arg)) if arg is not None else None
        self._ptr = Asst._lib.AsstCreateEx(callback, c_arg)
        if not self._ptr:
            raise RuntimeError("AsstCreateEx 返回空 handle")

    def append_task(self, type_name: str, params: Optional[dict] = None) -> int:
        body = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")
        return int(Asst._lib.AsstAppendTask(self._ptr, type_name.encode("utf-8"), body))

    def start(self) -> bool:
        return bool(Asst._lib.AsstStart(self._ptr))

    def stop(self) -> bool:
        return bool(Asst._lib.AsstStop(self._ptr))

    def async_connect(
        self, adb_path: str, address: str, config: str = "General", block: bool = False
    ) -> int:
        return int(
            Asst._lib.AsstAsyncConnect(
                self._ptr,
                adb_path.encode("utf-8"),
                address.encode("utf-8"),
                config.encode("utf-8"),
                bool(block),
            )
        )

    def get_version(self) -> str:
        raw = Asst._lib.AsstGetVersion()
        return raw.decode("utf-8", "replace") if raw else ""

    def destroy(self) -> None:
        if getattr(self, "_ptr", None):
            Asst._lib.AsstDestroy(self._ptr)
            self._ptr = None


def _bind_lib(lib: Any) -> None:
    """显式声明 restype/argtypes：64 位下不声明 restype，指针会被截断成 int。"""
    lib.AsstSetUserDir.restype = ctypes.c_bool
    lib.AsstSetUserDir.argtypes = (ctypes.c_char_p,)

    lib.AsstLoadResource.restype = ctypes.c_bool
    lib.AsstLoadResource.argtypes = (ctypes.c_char_p,)

    lib.AsstCreateEx.restype = ctypes.c_void_p
    lib.AsstCreateEx.argtypes = (ctypes.c_void_p, ctypes.c_void_p)

    lib.AsstDestroy.restype = None
    lib.AsstDestroy.argtypes = (ctypes.c_void_p,)

    lib.AsstAppendTask.restype = ctypes.c_int
    lib.AsstAppendTask.argtypes = (ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p)

    lib.AsstStart.restype = ctypes.c_bool
    lib.AsstStart.argtypes = (ctypes.c_void_p,)

    lib.AsstStop.restype = ctypes.c_bool
    lib.AsstStop.argtypes = (ctypes.c_void_p,)

    lib.AsstAsyncConnect.restype = ctypes.c_int
    lib.AsstAsyncConnect.argtypes = (
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_bool,
    )

    lib.AsstGetVersion.restype = ctypes.c_char_p
    lib.AsstGetVersion.argtypes = ()


# ── 子进程侧 ───────────────────────────────────────────────────────────────


def _put(event_queue: Any, event_type: str, payload: dict) -> None:
    """写事件到主进程；任何异常都吞掉（可能运行在回调线程上）。"""
    try:
        event_queue.put({"type": event_type, "ts": time.time(), "payload": payload})
    except Exception:
        pass


@Asst.CallBackType
def _callback_bridge(msg: Any, details: Any, arg: Any) -> None:
    """运行在 MaaCore 自己的回调线程上：只做 json.loads + put，绝不抛异常。

    与旧实现（``callback_handler.handle_message``）的关键差异：不在回调线程里做业务
    判断、不写日志、不 raise——异常穿过 C 调用边界是未定义行为（docs/03 §3.2）。
    """
    global _CALLBACK_ENQUEUED, _CALLBACK_PUT_FAILED
    try:
        event_queue = _EVENT_QUEUE
        if event_queue is None:
            return
        raw = details.decode("utf-8", "replace") if details else "{}"
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"_unparsed": raw}
        event_queue.put(
            {
                "type": EVENT_CALLBACK,
                "ts": time.time(),
                "payload": {"msg": int(msg), "details": parsed},
            }
        )
        _CALLBACK_ENQUEUED += 1
    except Exception:
        _CALLBACK_PUT_FAILED += 1  # 回调线程内绝不向外抛异常


def _core_child(cmd_queue: Any, event_queue: Any, boot: dict) -> None:
    """子进程入口：必须是模块级函数，spawn 才能按限定名 pickle 它。"""
    global _EVENT_QUEUE
    _EVENT_QUEUE = event_queue
    child_t0 = time.monotonic()

    # 第一个事件就是「spawn 成功」的证据：主进程不必只看 Process.start() 不报错。
    _put(
        event_queue,
        EVENT_LOG,
        {
            "level": "INFO",
            "content": "child boot",
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "child_entry_ts": time.time(),
        },
    )

    asst = None
    try:
        # 启动序列里没有版本检查、没有下载、没有设备连接（docs/03 §3.1）。
        t = time.monotonic()
        loaded = Asst.load(path=boot["maa_path"], user_dir=boot.get("user_dir"))
        kernel_load_sec = time.monotonic() - t
        if not loaded:
            raise RuntimeError("Asst.load 返回 False（dlopen 或资源加载失败）")

        t = time.monotonic()
        asst = Asst(callback=_callback_bridge)
        asst_create_sec = time.monotonic() - t

        _put(
            event_queue,
            EVENT_READY,
            {
                "pid": os.getpid(),
                "version": asst.get_version(),
                "kernel_load_sec": round(kernel_load_sec, 4),
                "asst_create_sec": round(asst_create_sec, 4),
                "child_startup_sec": round(time.monotonic() - child_t0, 4),
                "python": platform.python_version(),
                "dyld_library_path": os.environ.get("DYLD_LIBRARY_PATH", ""),
            },
        )
    except Exception as exc:
        _put(
            event_queue,
            EVENT_FATAL,
            {
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            },
        )
        return

    _command_loop(asst, cmd_queue, event_queue)


def _command_loop(asst: Asst, cmd_queue: Any, event_queue: Any) -> None:
    """单线程串行命令循环：天然保证对 Asst 实例的调用不并发（docs/03 §3.3）。"""
    while True:
        try:
            cmd = cmd_queue.get()
        except (EOFError, OSError):
            return  # 主进程侧队列已关，视作退出信号

        cmd_id = cmd.get("cmd_id")
        cmd_type = cmd.get("type")
        payload = cmd.get("payload") or {}

        if cmd_type == CMD_SHUTDOWN:
            try:
                asst.stop()
            except Exception:
                pass
            _put(event_queue, EVENT_CMD_RESULT, {"cmd_id": cmd_id, "ok": True, "data": {}})
            asst.destroy()
            return

        try:
            data = _dispatch(asst, cmd_type, payload)
            _put(
                event_queue,
                EVENT_CMD_RESULT,
                {"cmd_id": cmd_id, "ok": True, "data": data},
            )
        except Exception as exc:
            _put(
                event_queue,
                EVENT_CMD_RESULT,
                {
                    "cmd_id": cmd_id,
                    "ok": False,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                },
            )


def _dispatch(asst: Asst, cmd_type: str, payload: dict) -> dict:
    if cmd_type == CMD_CONNECT:
        async_call_id = asst.async_connect(
            payload["adb_path"],
            payload["address"],
            payload.get("config", "General"),
            payload.get("block", False),
        )
        return {"async_call_id": async_call_id}
    if cmd_type == CMD_APPEND_TASK:
        task_id = asst.append_task(payload["type_name"], payload.get("params") or {})
        return {"task_id": task_id}
    if cmd_type == CMD_START:
        return {"started": asst.start()}
    if cmd_type == CMD_PING:
        # 顺带当 liveness/统计探针：把子进程侧已入队的回调条数报给主进程，
        # 用于核对「Queue 一条没丢」（docs/02 §3.1 的 PING，后续心跳会用到）。
        return {
            "seq": payload.get("seq"),
            "callback_enqueued": _CALLBACK_ENQUEUED,
            "callback_put_failed": _CALLBACK_PUT_FAILED,
        }
    raise ValueError(f"未知命令类型: {cmd_type}")


# ── 主进程侧 ───────────────────────────────────────────────────────────────


class ChildChannel:
    """一对 Queue + 单调游标的事件消费器。

    游标只前进：每条事件只交给一个等待者，因此「START 之后才到的回调」不会被 CONNECT
    阶段的回调冒名顶替（想要全量序列直接读 ``events``）。
    """

    def __init__(self, ctx: Any) -> None:
        self.cmd_queue = ctx.Queue()
        self.event_queue = ctx.Queue()
        self.events: list[dict] = []
        self._cursor = 0

    def send(self, cmd_type: str, payload: Optional[dict] = None) -> str:
        cmd_id = f"{cmd_type}-{uuid.uuid4().hex[:8]}"
        self.cmd_queue.put({"cmd_id": cmd_id, "type": cmd_type, "payload": payload or {}})
        return cmd_id

    def drain_until(
        self, predicate: Callable[[dict], bool], timeout: float
    ) -> Optional[dict]:
        while self._cursor < len(self.events):
            event = self.events[self._cursor]
            self._cursor += 1
            if predicate(event):
                return event

        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                event = self.event_queue.get(timeout=remaining)
            except queue_module.Empty:
                return None
            self.events.append(event)
            self._cursor = len(self.events)
            if predicate(event):
                return event

    def drain_now(self) -> int:
        """非阻塞地把管道里已有的事件收进 events（不推进游标，仍可被后续等待者检查）。"""
        drained = 0
        while True:
            try:
                event = self.event_queue.get_nowait()
            except (queue_module.Empty, OSError):
                return drained
            self.events.append(event)
            drained += 1

    def wait_result(self, cmd_id: str, timeout: float) -> Optional[dict]:
        def predicate(event: dict) -> bool:
            return (
                event.get("type") == EVENT_CMD_RESULT
                and event.get("payload", {}).get("cmd_id") == cmd_id
            )

        return self.drain_until(predicate, timeout)

    def callbacks(self) -> list[dict]:
        return [e for e in self.events if e.get("type") == EVENT_CALLBACK]

    def close(self) -> None:
        for q in (self.cmd_queue, self.event_queue):
            try:
                q.cancel_join_thread()  # 子进程可能已被 SIGKILL，不能等 feeder 线程
            except Exception:
                pass
            try:
                q.close()
            except Exception:
                pass


def _is_callback(event: dict) -> bool:
    return event.get("type") == EVENT_CALLBACK


def _msg_of(event: dict) -> Optional[int]:
    try:
        return int(event["payload"]["msg"])
    except Exception:
        return None


def _msg_name(msg: Optional[int]) -> str:
    if msg is None:
        return "?"
    return MSG_NAMES.get(msg, f"Unknown({msg})")


def _brief(details: Any, limit: int = 160) -> str:
    try:
        text = json.dumps(details, ensure_ascii=False)
    except Exception:
        text = repr(details)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _spawn_child(ctx: Any, boot: dict, tag: str) -> tuple[Any, ChildChannel]:
    channel = ChildChannel(ctx)
    proc = ctx.Process(
        target=_core_child,
        args=(channel.cmd_queue, channel.event_queue, boot),
        name=f"maa-core-spike-{tag}",
    )
    proc.start()
    return proc, channel


def _kill(proc: Any, timeout: float = 10.0) -> Optional[int]:
    """SIGKILL（不可捕获，模拟强杀），返回 exitcode。"""
    if proc.pid is not None and proc.is_alive():
        os.kill(proc.pid, signal.SIGKILL)
    proc.join(timeout)
    if proc.is_alive():  # 兜底：正常情况不会走到
        proc.terminate()
        proc.join(5)
    return proc.exitcode


def _alive(proc: Any) -> bool:
    try:
        return bool(proc.is_alive())
    except Exception:
        return False


def run_spike(args: argparse.Namespace) -> dict:
    maa_path = Path(args.maa_path).expanduser().resolve()
    lib_path = maa_path / LIB_FILENAMES.get(platform.system(), "libMaaCore.dylib")
    if not lib_path.is_file():
        raise FileNotFoundError(f"--maa-path 下找不到内核库: {lib_path}")

    # user_dir 用临时目录（必须已存在，否则 AsstSetUserDir 返回 False）：
    # 不污染仓库，也不让重复运行互相踩日志。
    user_dir = Path(tempfile.mkdtemp(prefix="maa-spike-user-"))

    ctx = multiprocessing.get_context("spawn")
    boot = {"maa_path": str(maa_path), "user_dir": str(user_dir)}

    result: dict = {
        "spawn_child": False,
        "kernel_loaded_in_child": False,
        "callback_crossed_queue": False,
        "task_accepted": False,
        "kill_detected": False,
        "restart_observed": False,
    }
    obs: dict = {
        "spike": "M1-02 子进程隔离最小验证",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "maa_path": str(maa_path),
        "lib_path": str(lib_path),
        "user_dir": str(user_dir),
        "adb": args.adb,
        "address": args.address,
        "task": args.task,
        "task_params": args.task_params,
        "timeout_sec": args.timeout,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "start_method": ctx.get_start_method(),
        "executable": sys.executable,
        "dyld_library_path_at_parent_start": os.environ.get("DYLD_LIBRARY_PATH", ""),
        "exitcode": None,
        "exitcode_signal": None,
        "task_id": None,
        "callbacks_after_start": False,
        "taskchain_started": False,
        "async_call_ret": None,
        "callback_msgs": [],
        "callback_msg_names": [],
        "callback_digest": [],
        "callback_samples": [],
        "connection_info_whats": [],
        "timings_sec": {},
        "child_reported": {},
        "restart_child_reported": {},
        "leftover_children": [],
        "errors": [],
        "notes": [
            "子进程启动序列不含版本检查/下载/设备连接（docs/03 §3.1）",
            "CONNECT 由主进程在 READY 之后显式下发，不在启动序列里（docs/03 §3.1）",
            "内核 v6.17.5 校验任务必填参数：StartUp 不带 client_type 时 APPEND_TASK 返回 0",
            "start_method=spawn：子进程按限定名重新 import 本模块，入口必须是模块级函数",
            "第二条子进程用全新的 Queue，避免与第一条的残留事件混淆",
        ],
    }

    child1 = None
    child2 = None
    channel1 = None
    channel2 = None
    t_run_start = time.monotonic()
    cmd_wait = min(30.0, args.timeout)
    callback_wait = min(30.0, args.timeout)

    def log(msg: str) -> None:
        print(f"[spike +{time.monotonic() - t_run_start:6.2f}s] {msg}", flush=True)

    def fail(msg: str) -> None:
        obs["errors"].append(msg)
        log(f"ERROR: {msg}")

    try:
        # ── 检查点 1：spawn 子进程 ────────────────────────────────────────
        log("spawn #1 …")
        t0 = time.monotonic()
        child1, channel1 = _spawn_child(ctx, boot, "1")
        obs["child_pid"] = child1.pid
        obs["timings_sec"]["spawn_call_sec"] = round(time.monotonic() - t0, 4)

        first = channel1.drain_until(
            lambda e: e.get("type") in (EVENT_LOG, EVENT_READY, EVENT_FATAL),
            args.timeout,
        )
        if first is None:
            raise TimeoutError(f"{args.timeout}s 内没有收到子进程任何事件（spawn 失败？）")
        result["spawn_child"] = True
        obs["timings_sec"]["spawn_to_first_event_sec"] = round(time.monotonic() - t0, 4)
        log(f"spawn OK pid={child1.pid} 首个事件 type={first['type']}")

        # ── 检查点 2：真实内核在子进程内加载完成 ──────────────────────────
        if first.get("type") != EVENT_READY:
            ready = channel1.drain_until(
                lambda e: e.get("type") in (EVENT_READY, EVENT_FATAL), args.timeout
            )
        else:
            ready = first
        if ready is None:
            raise TimeoutError(f"{args.timeout}s 内没有收到 READY")
        if ready.get("type") == EVENT_FATAL:
            payload = ready.get("payload", {})
            raise RuntimeError(f"子进程加载内核失败: {payload.get('error')}")
        result["kernel_loaded_in_child"] = True
        obs["timings_sec"]["spawn_to_ready_sec"] = round(time.monotonic() - t0, 4)
        obs["child_reported"] = dict(ready.get("payload", {}))
        obs["kernel_version"] = ready.get("payload", {}).get("version", "")
        log(
            "READY version={v} kernel_load={k}s asst_create={c}s child_startup={s}s".format(
                v=obs["kernel_version"],
                k=ready.get("payload", {}).get("kernel_load_sec"),
                c=ready.get("payload", {}).get("asst_create_sec"),
                s=ready.get("payload", {}).get("child_startup_sec"),
            )
        )

        # ── 设备连接（docs/03 §3.1：READY 之后由主进程显式下发） ──────────
        # 本卡主线只要求 APPEND_TASK/START，但没有连接时任务链拿不到设备；
        # docs/12 的 M1 可交付状态也把「连接设备」列进了最小链路，故补这条命令。
        t0 = time.monotonic()
        cmd_id = channel1.send(
            CMD_CONNECT,
            {
                "adb_path": args.adb,
                "address": args.address,
                "config": "General",
                "block": False,
            },
        )
        res = channel1.wait_result(cmd_id, cmd_wait)
        if res is None:
            fail(f"{cmd_wait}s 内没收到 CONNECT 的 CMD_RESULT")
        elif not res["payload"].get("ok"):
            fail(f"CONNECT 失败: {res['payload'].get('error')}")
        else:
            obs["timings_sec"]["connect_result_sec"] = round(time.monotonic() - t0, 4)
            obs["async_call_id"] = res["payload"]["data"].get("async_call_id")

        conn_ev = channel1.drain_until(
            lambda e: _is_callback(e) and _msg_of(e) == MSG_CONNECTION_INFO,
            callback_wait,
        )
        if conn_ev is None:
            fail(f"{callback_wait}s 内没收到 ConnectionInfo 回调")
        else:
            obs["timings_sec"]["first_connection_info_sec"] = round(
                time.monotonic() - t0, 4
            )
            log(f"ConnectionInfo: {_brief(conn_ev['payload']['details'])}")

        # ── 检查点 3：任务被内核接受 ─────────────────────────────────────
        t0 = time.monotonic()
        cmd_id = channel1.send(
            CMD_APPEND_TASK,
            {"type_name": args.task, "params": args.task_params},
        )
        res = channel1.wait_result(cmd_id, cmd_wait)
        if res is None:
            fail(f"{cmd_wait}s 内没收到 APPEND_TASK 的 CMD_RESULT")
        else:
            data = res["payload"].get("data") or {}
            obs["task_id"] = data.get("task_id")
            obs["timings_sec"]["append_task_result_sec"] = round(
                time.monotonic() - t0, 4
            )
            if res["payload"].get("ok") and data.get("task_id"):
                result["task_accepted"] = True
                log(
                    f"APPEND_TASK({args.task}, {args.task_params}) -> "
                    f"task_id={data.get('task_id')}"
                )
            else:
                fail(
                    f"APPEND_TASK({args.task}) 未被接受（task_id={data.get('task_id')}）；"
                    "v6.17.5 会校验必填参数，用 --task-params 补齐后重试"
                )

        # ── 检查点 4：回调跨 Queue 回到主进程 ────────────────────────────
        t0 = time.monotonic()
        cmd_id = channel1.send(CMD_START)
        res = channel1.wait_result(cmd_id, cmd_wait)
        if res is None:
            fail(f"{cmd_wait}s 内没收到 START 的 CMD_RESULT")
        elif not res["payload"].get("ok"):
            fail(f"START 失败: {res['payload'].get('error')}")
        else:
            obs["start_returned"] = (res["payload"].get("data") or {}).get("started")
            log(f"START -> {obs['start_returned']}")

        cb_ev = channel1.drain_until(_is_callback, callback_wait)
        if cb_ev is None:
            fail(f"START 之后 {callback_wait}s 内没有新的 CALLBACK 跨队列到达")
        else:
            obs["callbacks_after_start"] = True
            obs["timings_sec"]["first_callback_after_start_sec"] = round(
                time.monotonic() - t0, 4
            )
            log(f"回调跨进程 OK: msg={_msg_of(cb_ev)} {_msg_name(_msg_of(cb_ev))}")

        # 检查点的判定口径按本卡：ConnectionInfo 也算一条跨进程回调。
        # 上面那次等待是为了拿到「START 之后」的延迟数字，这里再按全量事件定论。
        all_callbacks = channel1.callbacks()
        if all_callbacks:
            result["callback_crossed_queue"] = True
            obs["taskchain_started"] = any(
                _msg_of(e) == MSG_TASK_CHAIN_START for e in all_callbacks
            )
            async_info = next(
                (e for e in all_callbacks if _msg_of(e) == MSG_ASYNC_CALL_INFO), None
            )
            if async_info is not None:
                obs["async_call_ret"] = (
                    async_info.get("payload", {}).get("details", {}) or {}
                ).get("details", {}).get("ret")
            log(
                f"累计收到 {len(all_callbacks)} 条跨进程回调: "
                f"{[_msg_name(_msg_of(e)) for e in all_callbacks]}"
            )
        else:
            fail("整个生命周期内没有任何 CALLBACK 跨队列到达")

        # 观察窗口：多收几条 SubTask* 回调。payload 越复杂（SubTaskExtraInfo 里是
        # 多层嵌套的 result/first/finished_tasks），对「Queue 能否 pickle 往返」的验证越充分。
        # 实测 StartUp 链走完（TaskChainCompleted + AllTasksCompleted）约需 8 秒。
        time.sleep(8.0)

        # ── 回调完整性：子进程自报入队条数 vs 主进程实收条数 ─────────────
        cmd_id = channel1.send(CMD_PING, {"seq": 1})
        res = channel1.wait_result(cmd_id, cmd_wait)
        if res is None:
            fail(f"{cmd_wait}s 内没收到 PING 的 CMD_RESULT")
        elif not res["payload"].get("ok"):
            fail(f"PING 失败: {res['payload'].get('error')}")
        else:
            data = res["payload"].get("data") or {}
            channel1.drain_now()
            received = len(channel1.callbacks())
            obs["callback_completeness"] = {
                "child_enqueued": data.get("callback_enqueued"),
                "child_put_failed": data.get("callback_put_failed"),
                "parent_received_after_ping": received,
                "match": bool(
                    data.get("callback_put_failed") == 0
                    and received >= (data.get("callback_enqueued") or 0)
                ),
            }
            log(f"回调完整性: {obs['callback_completeness']}")
            if not obs["callback_completeness"]["match"]:
                fail("回调跨进程有丢失（子进程入队数 > 主进程实收数）")

        # ── 检查点 5：SIGKILL 杀子进程并检测到 exitcode ──────────────────
        t0 = time.monotonic()
        log(f"SIGKILL pid={child1.pid}")
        exitcode = _kill(child1)
        obs["exitcode"] = exitcode
        if exitcode is not None and exitcode < 0:
            try:
                obs["exitcode_signal"] = signal.Signals(-exitcode).name
            except Exception:
                obs["exitcode_signal"] = f"signal {-exitcode}"
        obs["timings_sec"]["kill_to_exitcode_sec"] = round(time.monotonic() - t0, 4)
        if exitcode == -signal.SIGKILL:
            result["kill_detected"] = True
            log(f"kill 检测 OK exitcode={exitcode} ({obs['exitcode_signal']})")
        else:
            fail(f"期望 exitcode=-{int(signal.SIGKILL)}，实际 {exitcode}")

        # ── 检查点 6：重启（第二条子进程）并等到 READY ───────────────────
        log("spawn #2（重启观测）…")
        t0 = time.monotonic()
        child2, channel2 = _spawn_child(ctx, boot, "2")
        obs["restart_child_pid"] = child2.pid
        first2 = channel2.drain_until(
            lambda e: e.get("type") in (EVENT_LOG, EVENT_READY, EVENT_FATAL),
            args.timeout,
        )
        if first2 is None:
            fail(f"重启子进程 {args.timeout}s 内无任何事件")
        else:
            if first2.get("type") != EVENT_READY:
                ready2 = channel2.drain_until(
                    lambda e: e.get("type") in (EVENT_READY, EVENT_FATAL),
                    args.timeout,
                )
            else:
                ready2 = first2
            if ready2 is not None and ready2.get("type") == EVENT_READY:
                result["restart_observed"] = True
                obs["timings_sec"]["restart_spawn_to_ready_sec"] = round(
                    time.monotonic() - t0, 4
                )
                obs["restart_child_reported"] = dict(ready2.get("payload", {}))
                log(
                    "重启 READY pid={p} kernel_load={k}s".format(
                        p=ready2.get("payload", {}).get("pid"),
                        k=ready2.get("payload", {}).get("kernel_load_sec"),
                    )
                )
            else:
                payload = (ready2 or {}).get("payload", {})
                fail(f"重启子进程未 READY: {payload.get('error')}")

    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")
        obs["traceback"] = traceback.format_exc()
    finally:
        # ── 清理：不残留子进程 ──────────────────────────────────────────
        if child2 is not None and _alive(child2):
            if channel2 is not None:
                cmd_id = channel2.send(CMD_SHUTDOWN, {"graceful": True})
                res = channel2.wait_result(cmd_id, 10.0)
                obs["restart_shutdown"] = (
                    "graceful" if res is not None else "timeout->SIGKILL"
                )
            if _alive(child2):
                _kill(child2)
        if child1 is not None and _alive(child1):
            _kill(child1)

        obs["leftover_children"] = [
            {"name": p.name, "pid": p.pid, "alive": _alive(p)}
            for p in (child1, child2)
            if p is not None
        ]
        if any(entry["alive"] for entry in obs["leftover_children"]):
            fail("有子进程未清理干净")
        for channel in (channel1, channel2):
            if channel is not None:
                channel.close()

    # ── 汇总回调序列 ────────────────────────────────────────────────────
    callbacks = channel1.callbacks() if channel1 else []
    obs["callback_msgs"] = [_msg_of(e) for e in callbacks]
    obs["callback_msg_names"] = [_msg_name(_msg_of(e)) for e in callbacks]
    obs["callback_digest"] = [
        {
            "msg": _msg_of(e),
            "name": _msg_name(_msg_of(e)),
            "brief": _brief(e.get("payload", {}).get("details")),
        }
        for e in callbacks[:200]
    ]
    for wanted in (
        MSG_CONNECTION_INFO,
        MSG_ASYNC_CALL_INFO,
        MSG_TASK_CHAIN_START,
        MSG_TASK_CHAIN_ERROR,
        MSG_SUB_TASK_START,
        MSG_SUB_TASK_EXTRA_INFO,
    ):
        sample = next((e for e in callbacks if _msg_of(e) == wanted), None)
        if sample is not None:
            obs["callback_samples"].append(
                {
                    "msg": wanted,
                    "name": _msg_name(wanted),
                    "payload": sample.get("payload", {}).get("details"),
                }
            )
    obs["connection_info_whats"] = [
        (e.get("payload", {}).get("details") or {}).get("what")
        for e in callbacks
        if _msg_of(e) == MSG_CONNECTION_INFO
    ]
    counts: dict[str, int] = {}
    for event in callbacks:
        name = _msg_name(_msg_of(event))
        counts[name] = counts.get(name, 0) + 1
    obs["callback_counts_by_name"] = counts
    obs["timings_sec"]["total_sec"] = round(time.monotonic() - t_run_start, 4)
    result["observations"] = obs
    result["generated_at"] = obs["generated_at"]
    result["args"] = {
        "maa_path": args.maa_path,
        "adb": args.adb,
        "address": args.address,
        "task": args.task,
        "task_params": args.task_params,
        "timeout": args.timeout,
        "out": str(args.out),
    }
    return result


# ── 输出 ───────────────────────────────────────────────────────────────────


CHECKPOINTS = (
    "spawn_child",
    "kernel_loaded_in_child",
    "callback_crossed_queue",
    "task_accepted",
    "kill_detected",
    "restart_observed",
)


def render_summary(result: dict) -> str:
    obs = result.get("observations", {})
    lines = ["", "═" * 72, "M1-02 子进程隔离最小验证 · 实测摘要", "═" * 72]
    for key in CHECKPOINTS:
        mark = "PASS" if result.get(key) is True else "FAIL"
        lines.append(f"  [{mark}] {key}")
    lines.append("-" * 72)
    lines.append(
        f"  exitcode (child#1)  : {obs.get('exitcode')} ({obs.get('exitcode_signal')})"
    )
    lines.append(f"  kernel version      : {obs.get('kernel_version')!r}")
    lines.append(
        f"  task                : {obs.get('task')} {obs.get('task_params')} "
        f"-> task_id={obs.get('task_id')}"
    )
    lines.append(f"  callbacks received  : {len(obs.get('callback_msgs', []))}")
    lines.append(f"  callback msg 序列   : {obs.get('callback_msg_names')}")
    lines.append(f"  ConnectionInfo what : {obs.get('connection_info_whats')}")
    lines.append("  阶段耗时 (秒):")
    for key, value in (obs.get("timings_sec") or {}).items():
        lines.append(f"    {key:34s} {value}")
    lines.append(f"  子进程自报          : {obs.get('child_reported')}")
    lines.append(f"  重启子进程自报      : {obs.get('restart_child_reported')}")
    lines.append(f"  残留子进程          : {obs.get('leftover_children')}")
    if obs.get("errors"):
        lines.append("  错误:")
        for err in obs["errors"]:
            lines.append(f"    - {err}")
    lines.append("═" * 72)
    return "\n".join(lines)


def _task_params(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"--task-params 不是合法 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--task-params 必须是 JSON 对象")
    return parsed


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="spike_subprocess_isolation.py",
        description=(
            "M1-02 丢弃式探针：spawn 子进程 → 子进程内加载真实 MaaCore → "
            "提交任务并收到跨进程回调 → SIGKILL 子进程 → 观察重启。"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "示例： DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin "
            ".venv/bin/python scripts/spike_subprocess_isolation.py"
        ),
    )
    parser.add_argument(
        "--maa-path",
        default="resource/lib/maa/Darwin",
        help="含 libMaaCore.dylib 与 resource/ 的目录",
    )
    parser.add_argument("--adb", default="/opt/homebrew/bin/adb", help="adb 可执行文件路径")
    parser.add_argument("--address", default="127.0.0.1:5555", help="adb 设备地址")
    parser.add_argument("--task", default="StartUp", help="要提交的最短任务名")
    parser.add_argument(
        "--task-params",
        type=_task_params,
        default=_task_params('{"client_type": "Official"}'),
        help=(
            "APPEND_TASK 的 params（JSON 对象）。默认值不是装饰：v6.17.5 会校验必填参数，"
            'StartUp 不带 client_type 时 AsstAppendTask 返回 0'
        ),
    )
    parser.add_argument(
        "--timeout", type=float, default=90.0, help="单次等待子进程的最长秒数"
    )
    parser.add_argument(
        "--out",
        default="tests/fixtures/spike_subprocess_result.json",
        help="结果 JSON 落点",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    out_path = Path(args.out).expanduser()

    print(
        "M1-02 spike: spawn child -> load real MaaCore -> cross-process callback "
        "-> SIGKILL -> restart",
        flush=True,
    )
    try:
        result = run_spike(args)
    except Exception as exc:  # 连子进程都没起来：仍然写出诚实的失败记录
        result = {
            "spawn_child": False,
            "kernel_loaded_in_child": False,
            "callback_crossed_queue": False,
            "task_accepted": False,
            "kill_detected": False,
            "restart_observed": False,
            "observations": {
                "errors": [f"{type(exc).__name__}: {exc}"],
                "traceback": traceback.format_exc(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "exitcode": None,
            },
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(render_summary(result), flush=True)
    print(f"结果已写入: {out_path}", flush=True)

    all_pass = all(result.get(key) is True for key in CHECKPOINTS)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
