#!/usr/bin/env python3
"""M1-13 命令行冒烟脚本：启动子进程 → 加载资源 → 连接 → 提交最短任务 → 收回调 → 杀进程观察重启。

这正是 docs/12 §M1 点名的「可交付状态」：**不经过 HTTP 层**，用一条命令把 M1 的
内核层全链路跑一遍（docs/12 §M1 / §5、docs/13 ADR-02）：

1. ``CoreSupervisor`` 以 spawn 起子进程（``maa_api.core.worker`` 的启动序列：
   装日志出口 → 加载基础资源 → 叠加增量资源 → 建实例 → 上报 READY），启动路径上没有
   网络 IO（docs/03 §3.1）；
2. ``CoreClient`` 起事件消费线程、注册 READY/FATAL/PONG 到 supervisor 状态机、
   两级 Future 连接设备（``msg=4`` 的 ``AsyncCallInfo`` 才算结果，``msg=2`` 的
   ``ConnectionInfo(Connected)`` 只是中间态；载荷层级照 M1-01 真机实测：
   顶层 ``async_call_id`` / ``what``，成功标志在 ``details.details.ret``）；
3. ``CoreRegistry`` 注册 ``core_id="default"``，之后的内核调用一律经
   ``registry.get(core_id)``（docs/02 §5.4）；
4. ``APPEND_TASK`` → ``START`` → 等终态回调（``TaskChainCompleted`` /
   ``TaskChainError`` / ``TaskChainStopped``）；
5. ``--kill``：在 ``acquire_maintenance()`` 维护窗口内 SIGKILL 子进程，打印
   「窗口内不按崩溃处理、不重启」这一事实；退出窗口后 supervisor 把这次死亡补记为崩溃
   （``CRASHED``）→ 退避 → ``RESTARTING`` → ``READY``，打印重启用时与新的 pid
   （docs/03 §4.3、docs/02 §4）；
6. 收尾：``client.stop()`` → ``sup.stop()`` → ``client.close()``，finally 再加一层
   ``maa-core`` 残留子进程兜底回收。成功最后一行打印 ``SMOKE OK`` 并以 0 退出，
   任何一步失败都以 1 退出并打印明确原因。

真机手动跑（HTTP 服务尚未接入新内核层，本脚本就是 M1 的验收入口）::

    DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
        .venv/bin/python scripts/core_smoke.py --kill

不带内核与设备的自检（CI 门禁用的那条，几秒内结束）::

    .venv/bin/python scripts/core_smoke.py --fake --timeout 30

``--fake`` 用 ``tests.fakes.fake_asst:FakeAsst`` 替身，因此不 dlopen 内核、不连设备；
它需要 ``tests.fakes.fake_asst`` 可导入，本脚本在模块顶层把仓库根插进 ``sys.path``，
spawn 子进程会继承这份 ``sys.path``（子进程按路径重新 import 本模块时也会再插一次）。
``--help`` 只解析参数：内核依赖全部在 ``main()`` 之后延迟 import，不加载动态库。

已知边界（照实测，不照文档推测）：

- ``StartUp`` 缺 ``client_type`` 时内核静默返回 ``task_id=0``（不抛异常、无回调，
  M1-02 实测），所以本脚本对 ``StartUp`` 自动补默认参数 ``{"client_type": "Official"}``；
- 自动重启只把子进程拉回 ``READY``，**不会**自动重新 ``CONNECT``（M1-08 语义，
  设备重连归 M6），所以 ``--kill`` 之后设备是断开状态，脚本不会再次提交任务；
- 维护窗口内退出/被杀的进程由 ``acquire_maintenance()`` 的退出钩子补记为崩溃，
  而不是窗口内的存活轮询（窗口内它被有意抑制）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import multiprocessing
import os
import shutil
import signal
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Optional

#: spawn 子进程会继承父进程的 ``sys.path``，但直接执行脚本时 ``sys.path[0]`` 是
#: ``scripts/``：不插仓库根，``--fake`` 子进程就 import 不到 ``tests.fakes.fake_asst``。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: CLI 默认值（都可用参数覆盖）。
DEFAULT_MAA_PATH = "resource/lib/maa/Darwin"
DEFAULT_ADB_PATH = "/opt/homebrew/bin/adb"
DEFAULT_ADDRESS = "127.0.0.1:5555"
DEFAULT_CONFIG = "General"
DEFAULT_TASK = "StartUp"
DEFAULT_TIMEOUT = 90.0

#: boot_config 的 asst_factory：真实内核封装 / M1-05 的替身（docs/13 ADR-02）。
REAL_ASST_FACTORY = "maa_api.core.asst:Asst"
FAKE_ASST_FACTORY = "tests.fakes.fake_asst:FakeAsst"

#: ``CoreSupervisor._spawn_process`` 给子进程起的名字，finally 兜底回收靠它匹配。
WORKER_PROCESS_NAME = "maa-core"

#: 收到即视为任务链结束的三条回调；只有 TaskChainCompleted 算成功。
TERMINAL_CALLBACKS = ("TaskChainCompleted", "TaskChainError", "TaskChainStopped")
SUCCESS_CALLBACK = "TaskChainCompleted"

#: 会逐条打印的回调类型（SubTask* 太密，只计数不逐条打印）。
INTERESTING_CALLBACKS = frozenset(
    {
        "ConnectionInfo",
        "AsyncCallInfo",
        "TaskChainStart",
        "TaskChainCompleted",
        "TaskChainError",
        "TaskChainStopped",
        "AllTasksCompleted",
    }
)

#: ``StartUp`` 这类任务缺少必填参数时内核返回 0（M1-02 实测），按任务类型补默认值。
DEFAULT_TASK_PARAMS: dict[str, dict] = {"StartUp": {"client_type": "Official"}}

_EPILOG = """\
示例:
  # 真机全链路: 连接 127.0.0.1:5555, 提交最短任务 StartUp, 并观察杀进程后的自动重启
  DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
      .venv/bin/python scripts/core_smoke.py --kill

  # 不加载内核/不连设备的自检 (FakeAsst 替身, 几秒内结束, 供门禁使用)
  .venv/bin/python scripts/core_smoke.py --fake --timeout 30

`--help` 只解析参数, 不加载内核、不启动子进程。
"""


class SmokeFailure(RuntimeError):
    """冒烟步骤失败（脚本内信号，统一转成退出码 1）。"""


# ----------------------------------------------------------------------
# 无副作用的小工具
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器（只解析参数，不 import 内核层）。"""
    parser = argparse.ArgumentParser(
        prog="core_smoke.py",
        description=(
            "M1-13 内核层命令行冒烟：启动子进程 → 加载资源 → 连接 → 提交最短任务 → "
            "收回调 →（--kill）杀进程观察重启。"
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--maa-path",
        default=DEFAULT_MAA_PATH,
        metavar="DIR",
        help=f"内核目录（含 libMaaCore.* 与 resource/），默认 {DEFAULT_MAA_PATH}",
    )
    parser.add_argument(
        "--user-dir",
        default=None,
        metavar="DIR",
        help="内核用户数据目录（日志 / crash.log），默认新建临时目录",
    )
    parser.add_argument(
        "--incremental",
        action="append",
        default=[],
        metavar="DIR",
        help="增量资源层根目录，可重复，顺序即优先级（--fake 下忽略）",
    )
    parser.add_argument(
        "--adb", default=DEFAULT_ADB_PATH, metavar="PATH", help=f"adb 可执行文件，默认 {DEFAULT_ADB_PATH}"
    )
    parser.add_argument(
        "--address", default=DEFAULT_ADDRESS, metavar="HOST:PORT", help=f"设备地址，默认 {DEFAULT_ADDRESS}"
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, metavar="NAME", help=f"AsstAsyncConnect 的配置名，默认 {DEFAULT_CONFIG}"
    )
    parser.add_argument(
        "--task", default=DEFAULT_TASK, metavar="TYPE", help=f"最短任务类型，默认 {DEFAULT_TASK}"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=(
            "各阶段等待上限（等待 READY / 连接结果 / 终态回调 / 杀进程后重启），"
            f"默认 {DEFAULT_TIMEOUT:g}"
        ),
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="用 tests.fakes.fake_asst:FakeAsst 替身：不加载内核、不连设备",
    )
    parser.add_argument(
        "--kill",
        action="store_true",
        help="任务跑起来后杀子进程，观察维护窗口语义与自动重启",
    )
    return parser


def _resolve_maa_path(raw: str) -> Path:
    """把 ``--maa-path`` 解析成绝对路径：先按 CWD，再退回仓库根（默认值的常见位置）。"""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    if (Path.cwd() / candidate).exists():
        return (Path.cwd() / candidate).resolve()
    if (REPO_ROOT / candidate).exists():
        return (REPO_ROOT / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


def _sigkill(pid: int) -> str:
    """对 pid 发 SIGKILL 并返回可读结果；不抛异常（进程可能已被回收）。"""
    try:
        os.kill(int(pid), signal.SIGKILL)
    except ProcessLookupError:
        return "ProcessLookupError：进程已被回收（先前的 kill 已生效）"
    except OSError as exc:  # noqa: BLE001 - 只用于打印，失败不影响主流程
        return f"{type(exc).__name__}: {exc}"
    return "SIGKILL 已发送"


def _reap_core_children() -> list[int]:
    """兜底：回收当前进程名下所有还活着的 ``maa-core`` 子进程，返回被杀的 pid 列表。"""
    killed: list[int] = []
    for child in multiprocessing.active_children():
        if child.name == WORKER_PROCESS_NAME and child.is_alive():
            child.kill()
            child.join(timeout=2.0)
            killed.append(int(child.pid))
    return killed


# ----------------------------------------------------------------------
# 冒烟主体
# ----------------------------------------------------------------------


class CoreSmoke:
    """一次冒烟运行的编排与观测（构造不碰内核，``run()`` 里才延迟 import）。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.user_dir: Optional[Path] = None
        self.created_user_dir = False
        self.maa_path: Optional[Path] = None

        self.supervisor: Any = None
        self.client: Any = None

        self.failures: list[str] = []
        self.callbacks: list[dict] = []
        self.callback_counts: dict[str, int] = {}
        self.state_changes: list[tuple[float, str]] = []
        self.crash_records: list[dict] = []
        self.terminal_callback: Optional[dict] = None

        self._message: Any = None
        self._terminal_event: Optional[asyncio.Event] = None

    # ---- 输出 ----

    @staticmethod
    def step(message: str) -> None:
        print(f"[core_smoke] {message}", flush=True)

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[core_smoke][FAIL] {message}", flush=True)

    # ---- 观测钩子（注册到 CoreClient / CoreSupervisor 的公开扩展点） ----

    def _on_state_change(self, state: Any) -> None:
        value = getattr(state, "value", str(state))
        self.state_changes.append((time.monotonic(), value))
        self.step(f"state → {value}")

    def _on_crash(self, record: dict) -> None:
        self.crash_records.append(dict(record))
        self.step(
            f"崩溃现场：reason={record.get('reason')!r} exitcode={record.get('exitcode')!r} "
            f"attempt={record.get('attempt')!r} pid={record.get('pid')!r}"
        )

    def _on_fatal(self, payload: dict) -> None:
        self.step(f"FATAL：{payload.get('error') or payload}")

    def _on_log(self, payload: dict) -> None:
        level = str(payload.get("level", "")).upper()
        if level in ("WARNING", "ERROR", "CRITICAL"):
            self.step(f"内核日志[{level}] {payload.get('content')}")

    def _on_callback(self, payload: dict) -> None:
        """收集 CALLBACK（payload 形态 ``{"msg": int, "details": dict}``，原始内核载荷）。"""
        raw = payload.get("msg")
        try:
            name = self._message(int(raw)).name
        except (TypeError, ValueError):
            name = f"UNKNOWN({raw!r})"
        record = {"msg": raw, "name": name, "details": payload.get("details")}
        self.callbacks.append(record)
        self.callback_counts[name] = self.callback_counts.get(name, 0) + 1
        if name in INTERESTING_CALLBACKS:
            hint = self._callback_hint(record)
            self.step(f"回调 {name}(msg={raw}){f' {hint}' if hint else ''}")
        if name in TERMINAL_CALLBACKS and self.terminal_callback is None:
            self.terminal_callback = record
            if self._terminal_event is not None:
                self._terminal_event.set()

    @staticmethod
    def _callback_hint(record: dict) -> str:
        """按 M1-01 实测层级打印关键字段（不参与判定，只做可见性）。"""
        details = record.get("details")
        if not isinstance(details, dict):
            return ""
        name = record.get("name")
        if name == "ConnectionInfo":
            return f"what={details.get('what')!r}（中间态，连接成败只认 msg=4）"
        if name == "AsyncCallInfo":
            nested = details.get("details")
            ret = nested.get("ret") if isinstance(nested, dict) else None
            return (
                f"what={details.get('what')!r} "
                f"async_call_id={details.get('async_call_id')!r}（顶层）"
                f" details.details.ret={ret!r}（双层嵌套）"
            )
        if name in (
            "TaskChainStart",
            "TaskChainCompleted",
            "TaskChainError",
            "TaskChainStopped",
        ):
            return f"taskchain={details.get('taskchain')!r} taskid={details.get('taskid')!r}"
        return ""

    # ---- 准备 ----

    def _prepare_paths(self) -> None:
        args = self.args
        if args.user_dir:
            self.user_dir = Path(args.user_dir).expanduser().resolve()
        else:
            self.user_dir = Path(tempfile.mkdtemp(prefix="maa-core-smoke-")).resolve()
            self.created_user_dir = True
        self.user_dir.mkdir(parents=True, exist_ok=True)

        if args.fake:
            return
        self.maa_path = _resolve_maa_path(args.maa_path)
        if not self.maa_path.is_dir():
            raise SmokeFailure(f"内核目录不存在：{self.maa_path}（用 --maa-path 指定）")
        if not any(self.maa_path.glob("libMaaCore.*")):
            raise SmokeFailure(f"内核目录里没有 libMaaCore.*：{self.maa_path}")

    def _build_boot_config(self) -> dict:
        """按 M1-07 的 boot_config 契约组装（纯数据、可 pickle）。"""
        args = self.args
        if args.fake:
            if args.incremental:
                self.step("--fake：忽略 --incremental（替身不读资源目录）")
            return {
                "maa_path": ".",
                "user_dir": str(self.user_dir),
                "incremental_paths": [],
                "instance_options": {},
                "asst_factory": FAKE_ASST_FACTORY,
                "asst_factory_kwargs": {"script": "success"},
            }
        return {
            "maa_path": str(self.maa_path),
            "user_dir": str(self.user_dir),
            "incremental_paths": [str(Path(item).expanduser()) for item in args.incremental],
            "instance_options": {},
            "asst_factory": REAL_ASST_FACTORY,
            "asst_factory_kwargs": {},
        }

    def _setup(self, boot_config: dict) -> None:
        """构造 supervisor / client 并接线（延迟 import：--help 不加载内核）。"""
        from maa_api.core.client import CoreClient
        from maa_api.core.enums import Message
        from maa_api.core.supervisor import CoreSupervisor

        self._message = Message
        self._terminal_event = asyncio.Event()

        supervisor = CoreSupervisor(
            boot_config,
            on_crash=self._on_crash,
            on_state_change=self._on_state_change,
        )
        client = CoreClient(
            supervisor,
            connect_timeout=float(self.args.timeout),
            accept_timeout=float(self.args.timeout),
        )
        # 卡面契约：READY / FATAL / PONG 明确接进 supervisor 状态机（CoreClient 构造时
        # 已默认接好，重复注册幂等无害；显式写出来让接线可见）。
        client.on("READY", supervisor.handle_ready)
        client.on("FATAL", supervisor.handle_fatal)
        client.on("PONG", supervisor.handle_pong)
        client.on("CALLBACK", self._on_callback)
        client.on("LOG", self._on_log)
        client.on("FATAL", self._on_fatal)

        self.supervisor = supervisor
        self.client = client

    # ---- 各阶段 ----

    async def _start_core(self) -> None:
        from maa_api.core.registry import DEFAULT_CORE_ID, CoreRegistry

        supervisor, client = self.supervisor, self.client
        self.step("启动子进程并等待 READY（启动序列：dlopen → 加载资源 → 建实例 → 选项，无网络 IO）")
        client.start_consumer()  # 声明 event_queue 的唯一消费者（supervisor 的启动门禁）
        started = time.monotonic()
        await supervisor.start(wait_ready=True, timeout=float(self.args.timeout))
        self.step(
            f"READY：pid={supervisor.pid} state={supervisor.state.value} "
            f"用时 {time.monotonic() - started:.2f}s"
        )

        registry = CoreRegistry()
        registry.register(DEFAULT_CORE_ID, client)
        client = registry.get(DEFAULT_CORE_ID)  # 之后的内核调用一律经注册表（docs/02 §5.4）
        self.client = client
        self.step(f"CoreRegistry：已注册 core_id={DEFAULT_CORE_ID!r}，之后用 registry.get(core_id) 取 CoreClient")

        version = await client.get_version()
        self.step(f"内核版本：{version!r}")
        if not version:
            self.fail("内核版本为空（GET_VERSION 返回空）")

    async def _connect(self) -> None:
        args = self.args
        if args.fake:
            self.step(f"连接设备：{args.address}（--fake：adb={args.adb} 与设备都被替身忽略）")
        else:
            self.step(f"连接设备：adb={args.adb} address={args.address} config={args.config}")
        started = time.monotonic()
        connected = await self.client.connect(
            args.adb, args.address, args.config, timeout=float(args.timeout)
        )
        self.step(
            f"连接结果：{connected}（两级 Future：CMD_RESULT 受理 + msg=4 AsyncCallInfo，"
            f"用时 {time.monotonic() - started:.2f}s）"
        )
        if not connected:
            raise SmokeFailure("连接失败：AsyncCallInfo 的 details.details.ret 为 False")

    async def _submit_task(self) -> None:
        args = self.args
        params = dict(DEFAULT_TASK_PARAMS.get(args.task, {}))
        self.step(f"提交最短任务：{args.task} params={params}")
        task_id = await self.client.append_task(args.task, params)
        if not task_id:
            raise SmokeFailure(
                f"APPEND_TASK({args.task!r}) 返回 0：内核拒绝了任务（多为缺必填参数，"
                "M1-02 实测不会抛异常也不会有回调）"
            )
        self.step(f"task_id={task_id}；START 启动任务链")
        if not await self.client.start():
            raise SmokeFailure("START 返回 False，任务链未启动")

        self.step(
            f"等待终态回调（{' / '.join(TERMINAL_CALLBACKS)}），上限 {args.timeout:g}s ..."
        )
        started = time.monotonic()
        waiter = self._terminal_event
        if waiter is None:
            raise SmokeFailure("内部错误：终态回调等待器未初始化（_setup 未执行）")
        try:
            await asyncio.wait_for(waiter.wait(), timeout=float(args.timeout))
        except asyncio.TimeoutError:
            raise SmokeFailure(
                f"等待终态回调超时（{args.timeout:g}s）；已收到回调统计：{self._callback_summary()}"
            ) from None
        record = self.terminal_callback
        if record is None:
            raise SmokeFailure("内部错误：终态回调等待器被唤醒但没有记录到回调")
        name = str(record["name"])
        self.step(f"收到终态回调：{name}（用时 {time.monotonic() - started:.2f}s）")
        if name != SUCCESS_CALLBACK:
            self.fail(f"任务链未成功完成：终态回调是 {name}（TaskChainStopped 也算收到回调，但按失败计）")

    async def _kill_and_watch_restart(self) -> None:
        """维护窗口内杀 → 窗口外再杀一次（幂等确认）→ 观察 CRASHED → RESTARTING → READY。"""
        from maa_api.core.supervisor import CoreState

        supervisor = self.supervisor
        timeout = max(float(self.args.timeout), 1.0)
        old_pid = supervisor.pid
        if old_pid is None:
            raise SmokeFailure("--kill 需要先有 READY 的子进程，当前 pid 为空")

        self.step(f"--- --kill：维护窗口内 SIGKILL pid={old_pid}（窗口内的停止是预期行为，不按崩溃算）")
        async with supervisor.acquire_maintenance():
            first = _sigkill(old_pid)
            self.step(f"维护窗口内 kill(pid={old_pid}, SIGKILL)：{first}")
            # 存活轮询 0.05s 一次（M1-02 实测 SIGKILL→exitcode 约 0.05s），留足观测时间。
            await asyncio.sleep(min(0.5, timeout))
            in_window_state = supervisor.state
            self.step(
                f"维护窗口内观测：state={in_window_state.value} "
                f"in_maintenance={supervisor.in_maintenance} "
                f"last_crash={'有' if supervisor.last_crash else '无'}（不重启）"
            )
            if in_window_state is not CoreState.READY:
                self.fail(
                    f"维护窗口内状态应保持 READY（主动杀进程不该被判成崩溃），实际 {in_window_state.value}"
                )
            if supervisor.last_crash is not None:
                self.fail(f"维护窗口内不应产生崩溃现场：{supervisor.last_crash}")

        self.step("退出维护窗口：supervisor 应把窗口内死掉的子进程补记为崩溃，并走退避自动重启")
        # 卡面的「退出窗口再杀一次」：第一次 kill 已经杀死了它，这一枪是幂等确认；
        # 若进程已被回收则如实记录 ProcessLookupError，不影响 CRASHED 的补记路径。
        second = _sigkill(old_pid)
        self.step(f"退出窗口后再次 kill(pid={old_pid}, SIGKILL)：{second}")

        crash_at = time.monotonic()
        deadline = crash_at + timeout
        seen: list[str] = []
        while time.monotonic() < deadline:
            state = supervisor.state
            if not seen or seen[-1] != state.value:
                seen.append(state.value)
                self.step(
                    f"重启观测：state → {state.value}（pid={supervisor.pid}，"
                    f"t+{time.monotonic() - crash_at:.2f}s）"
                )
            if state is CoreState.READY and supervisor.pid != old_pid:
                break
            if state is CoreState.FAILED:
                break
            await asyncio.sleep(0.02)
        restarted_at = time.monotonic()

        final_state = supervisor.state
        new_pid = supervisor.pid
        self.step(
            f"重启结果：state={final_state.value} 新 pid={new_pid}（旧 pid={old_pid}）"
            f" 用时 {restarted_at - crash_at:.2f}s；状态序列={seen}"
        )
        if final_state is not CoreState.READY or new_pid == old_pid:
            raise SmokeFailure(
                f"杀进程后未观察到自动重启到 READY：state={final_state.value} "
                f"pid={new_pid}（旧 {old_pid}）状态序列={seen}"
            )
        for required in (CoreState.CRASHED.value, CoreState.RESTARTING.value):
            if required not in seen:
                self.fail(f"重启过程未观测到 {required} 状态（状态序列={seen}）")
        self.step("注意：自动重启只恢复子进程与 READY，不会自动重新 CONNECT（设备重连归 M6）")

    # ---- 收尾 ----

    async def _teardown(self) -> None:
        """client.stop() → sup.stop() → client.close()，最后兜底 kill 残留子进程。"""
        timeout = max(float(self.args.timeout), 1.0)
        client, supervisor = self.client, self.supervisor

        if client is not None:
            try:
                stopped = await asyncio.wait_for(client.stop(), timeout=min(timeout, 30.0))
                self.step(f"client.stop() → {stopped}（STOPPED）")
            except Exception as exc:  # noqa: BLE001 - 收尾失败不能掩盖主流程的失败原因
                self.step(f"client.stop() 跳过/失败：{type(exc).__name__}: {exc}")

        if supervisor is not None:
            try:
                await supervisor.stop(graceful=True, timeout=min(timeout, 10.0))
                self.step(
                    f"sup.stop() → state={supervisor.state.value} exitcode={supervisor.exitcode}"
                )
            except Exception as exc:  # noqa: BLE001 - 同上，走兜底 kill
                self.step(f"sup.stop() 失败，走兜底 kill：{type(exc).__name__}: {exc}")
                with contextlib.suppress(Exception):
                    await supervisor.stop(graceful=False, timeout=1.0)

        if client is not None:
            with contextlib.suppress(Exception):
                client.close()

        killed = _reap_core_children()
        if killed:
            self.step(f"兜底回收残留内核子进程：pid={killed}")
        else:
            self.step("确认无残留内核子进程")

    def _callback_summary(self) -> str:
        if not self.callback_counts:
            return "（一条都没有）"
        return ", ".join(f"{name}×{count}" for name, count in self.callback_counts.items())

    def _cleanup_user_dir(self) -> None:
        if not (self.created_user_dir and self.user_dir is not None):
            return
        if self.failures:
            self.step(f"保留临时用户目录以便排查：{self.user_dir}")
            return
        shutil.rmtree(self.user_dir, ignore_errors=True)
        self.step(f"已清理临时用户目录：{self.user_dir}")

    # ---- 入口 ----

    async def run(self) -> int:
        from maa_api.domain.errors import AppError  # 轻量：只依赖 stdlib 与枚举

        try:
            self._prepare_paths()
            boot_config = self._build_boot_config()
            self.step(
                "模式：" + ("FakeAsst 替身（不加载内核、不连设备）" if self.args.fake else "真实 MaaCore 内核")
            )
            self.step(f"boot_config：{boot_config}")
            self.step(f"用户数据目录：{self.user_dir}")
            self._setup(boot_config)
            await self._start_core()
            await self._connect()
            await self._submit_task()
            if self.args.kill:
                await self._kill_and_watch_restart()
        except SmokeFailure as exc:
            self.fail(str(exc))
        except AppError as exc:
            self.fail(f"内核层错误 {exc.code.value}: {exc.message}")
        except Exception as exc:  # noqa: BLE001 - 冒烟脚本必须自己收尾，绝不留子进程
            self.fail(f"未预期异常 {type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            with contextlib.suppress(Exception):
                await self._teardown()
            self._cleanup_user_dir()

        self.step(f"回调统计：{self._callback_summary()}")
        if self.failures:
            print(f"[core_smoke] 失败 {len(self.failures)} 项：", flush=True)
            for item in self.failures:
                print(f"  - {item}", flush=True)
            return 1
        self.step("全部检查通过")
        print("SMOKE OK", flush=True)
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    """解析参数并跑一次冒烟；``--help`` 在此直接返回，不加载内核。"""
    args = build_parser().parse_args(argv)
    smoke = CoreSmoke(args)
    try:
        return asyncio.run(smoke.run())
    except KeyboardInterrupt:
        print("[core_smoke] 收到中断（Ctrl-C），兜底回收子进程后退出", file=sys.stderr)
        for pid in _reap_core_children():
            print(f"[core_smoke] 已回收 pid={pid}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # spawn 子进程按路径重新 import 本模块时 __name__ != "__main__"
    raise SystemExit(main())
