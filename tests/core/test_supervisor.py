"""CoreSupervisor 的状态机、心跳、崩溃检测、退避重启与维护窗口测试（M1-08）。

不碰真实内核、不碰设备：spawn 出来的子进程跑真实的
:func:`maa_api.core.worker.core_worker_main`，但 ``boot_config["asst_factory"]``
指向纯 Python 替身 ``tests.fakes.fake_asst:FakeAsst``（docs/03 §8）。

消费者纪律：supervisor 自己不读 ``event_queue``。本文件用模块级线程函数
:func:`_dispatch_loop` 充当唯一消费者，把 ``READY`` / ``PONG`` / ``FATAL`` 转给
``handle_*``，其余事件交给 ``note_event`` 计入崩溃现场；``set_dispatcher`` 只做
「已有唯一消费者」的声明。

所有等待都有 ≤5 秒超时；每个用例结束都 ``stop()``，autouse fixture 再兜底回收
``maa-core`` 子进程，不留残留。
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import queue
import signal
import threading
import time
from typing import Any, Iterator

import pytest

from maa_api.core.protocol import make_command
from maa_api.core.supervisor import (
    BACKOFF_SECONDS,
    CRASH_EVENT_BUFFER,
    HEARTBEAT_FAILURES,
    HEARTBEAT_INTERVAL,
    LONG_COMMANDS,
    MAX_RESTART_ATTEMPTS,
    CoreState,
    CoreSupervisor,
)
from maa_api.domain.errors import AppError, ErrorCode

# ---------------------------------------------------------------------------
# 工具：boot_config / 唯一消费者线程 / 等待助手
# ---------------------------------------------------------------------------


def _boot_config(script: str = "success") -> dict:
    """最小 boot_config：FakeAsst 不读文件系统、不加载动态库。"""
    return {
        "maa_path": ".",
        "user_dir": None,
        "incremental_paths": [],
        "instance_options": {},
        "asst_factory": "tests.fakes.fake_asst:FakeAsst",
        "asst_factory_kwargs": {"script": script},
    }


def _dispatch_loop(
    supervisor: CoreSupervisor,
    stop_event: threading.Event,
    drop_pongs: threading.Event,
    counts: dict[str, int],
    cmd_results: list[dict],
    lock: threading.Lock,
) -> None:
    """测试内模块级假 dispatcher：唯一读 event_queue 的循环。

    - ``READY`` / ``FATAL`` / ``PONG`` → supervisor 的 handle_*；
    - ``drop_pongs`` 置位时丢弃 PONG（模拟「命令循环卡死、收不到 PONG」）；
    - 其余事件（``CALLBACK`` / ``LOG`` / ``CMD_RESULT``）只计数并 note_event。

    ``event_queue`` **每轮重新读** ``supervisor.event_queue``：队列按代轮换（内核每次
    重启换一对新 Queue），缓存 Queue 对象会一直阻塞在旧队列上。
    """
    while not stop_event.is_set():
        try:
            event = supervisor.event_queue.get(timeout=0.05)
        except queue.Empty:
            continue
        except (OSError, ValueError, EOFError):
            time.sleep(0.01)  # 队列被换掉 / 关闭：下一轮重新读属性
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        payload = event.get("payload") or {}
        with lock:
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "CMD_RESULT":
                cmd_results.append(payload)
        if kind == "READY":
            supervisor.handle_ready(payload)
        elif kind == "FATAL":
            supervisor.handle_fatal(payload)
        elif kind == "PONG":
            if drop_pongs.is_set():
                continue
            supervisor.handle_pong(payload)
        else:
            supervisor.note_event(event)


class _Consumer:
    """测试侧的唯一消费者（线程）封装。"""

    def __init__(self, supervisor: CoreSupervisor) -> None:
        self.supervisor = supervisor
        self.drop_pongs = threading.Event()
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.counts: dict[str, int] = {}
        self.cmd_results: list[dict] = []
        self.thread = threading.Thread(
            target=_dispatch_loop,
            args=(
                supervisor,
                self.stop_event,
                self.drop_pongs,
                self.counts,
                self.cmd_results,
                self._lock,
            ),
            name="test-core-consumer",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def close(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        self.thread.join(timeout=timeout)

    def count(self, kind: str) -> int:
        with self._lock:
            return self.counts.get(kind, 0)

    def results_for(self, cmd_id: str) -> list[dict]:
        with self._lock:
            return [item for item in self.cmd_results if item.get("cmd_id") == cmd_id]


def _new_supervisor(*, script: str = "success", **kwargs: Any) -> tuple[CoreSupervisor, _Consumer]:
    supervisor = CoreSupervisor(_boot_config(script), **kwargs)
    consumer = _Consumer(supervisor)
    consumer.start()
    # fn 只做「已有唯一消费者」的声明；真正的转交在 _dispatch_loop 里完成。
    supervisor.set_dispatcher(lambda event: None)
    return supervisor, consumer


async def _stop_supervisor(supervisor: CoreSupervisor) -> None:
    with contextlib.suppress(Exception):
        await asyncio.wait_for(supervisor.stop(graceful=False, timeout=2.0), timeout=20.0)


@contextlib.contextmanager
def _running(*, script: str = "success", **kwargs: Any) -> Iterator[tuple[CoreSupervisor, _Consumer]]:
    """起一个带消费者的 supervisor，退出时保证子进程被清理。"""
    supervisor, consumer = _new_supervisor(script=script, **kwargs)
    try:
        yield supervisor, consumer
    finally:
        asyncio.run(_stop_supervisor(supervisor))
        consumer.close()


@pytest.fixture(autouse=True)
def _reap_core_children() -> Iterator[None]:
    """兜底：任何用例结束后都不允许残留 maa-core 子进程。"""
    yield
    for child in multiprocessing.active_children():
        if child.name == "maa-core" and child.is_alive():
            child.kill()
            child.join(timeout=2.0)


async def _wait_state(
    supervisor: CoreSupervisor, state: CoreState, timeout: float = 5.0
) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if supervisor.state is state:
            return True
        await asyncio.sleep(0.01)
    return supervisor.state is state


async def _wait_new_ready(supervisor: CoreSupervisor, old_pid: int, timeout: float = 5.0) -> bool:
    """等重启完成：状态 READY 且 pid 与崩溃前不同（避免读到崩溃前的旧 READY）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if supervisor.state is CoreState.READY and supervisor.pid != old_pid:
            return True
        await asyncio.sleep(0.01)
    return False


def _wait_count(consumer: _Consumer, kind: str, minimum: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if consumer.count(kind) >= minimum:
            return True
        time.sleep(0.01)
    return consumer.count(kind) >= minimum


async def _wait_cmd_result(consumer: _Consumer, cmd_id: str, timeout: float = 5.0) -> bool:
    """等某条命令的 CMD_RESULT（按下发时的 cmd_id 匹配）。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if consumer.results_for(cmd_id):
            return True
        await asyncio.sleep(0.01)
    return bool(consumer.results_for(cmd_id))


# ---------------------------------------------------------------------------
# 公开契约
# ---------------------------------------------------------------------------


def test_public_contract_defaults() -> None:
    assert tuple(BACKOFF_SECONDS) == (5, 15, 60)
    assert MAX_RESTART_ATTEMPTS == 5
    assert HEARTBEAT_INTERVAL == 5.0 and HEARTBEAT_FAILURES == 3
    assert LONG_COMMANDS == frozenset({"LOAD_RESOURCE", "CONNECT"})
    assert CRASH_EVENT_BUFFER == 50
    assert {state.name for state in CoreState} >= {
        "STOPPED",
        "STARTING",
        "READY",
        "CRASHED",
        "RESTARTING",
        "FAILED",
    }
    supervisor = CoreSupervisor(_boot_config())
    assert supervisor.state is CoreState.STOPPED
    assert supervisor.in_maintenance is False
    assert supervisor.pid is None and supervisor.exitcode is None
    assert supervisor.last_crash is None
    assert supervisor.cmd_queue is not None and supervisor.event_queue is not None


def test_start_requires_dispatcher_fail_loud() -> None:
    supervisor = CoreSupervisor(_boot_config())
    assert supervisor.state is CoreState.STOPPED
    with pytest.raises(AppError) as excinfo:
        asyncio.run(supervisor.start(timeout=1.0))
    assert excinfo.value.code is ErrorCode.CORE_START_FAILED
    assert supervisor.state is CoreState.STOPPED  # fail loud 且不留下半个状态
    assert supervisor.pid is None


# ---------------------------------------------------------------------------
# ① 启动 → READY → 停止
# ---------------------------------------------------------------------------


def test_start_ready_then_stop_state_transitions() -> None:
    states: list[CoreState] = []
    crashes: list[dict] = []
    with _running(on_state_change=states.append, on_crash=crashes.append) as (
        supervisor,
        consumer,
    ):
        assert supervisor.state is CoreState.STOPPED
        asyncio.run(supervisor.start(timeout=5.0))
        assert supervisor.state is CoreState.READY
        assert isinstance(supervisor.pid, int)
        assert supervisor.exitcode is None  # 进程活着
        assert consumer.count("READY") == 1
        assert states[:2] == [CoreState.STARTING, CoreState.READY]

        asyncio.run(supervisor.stop())
        assert supervisor.state is CoreState.STOPPED
        assert supervisor.exitcode == 0  # worker 收到 SHUTDOWN 后正常退出
        assert states[-1] is CoreState.STOPPED
        assert crashes == [] and supervisor.last_crash is None


# ---------------------------------------------------------------------------
# ② 心跳超时 → CRASHED + 崩溃现场
# ---------------------------------------------------------------------------


def test_heartbeat_timeout_marks_crashed_with_scene() -> None:
    crashes: list[dict] = []
    with _running(
        heartbeat_interval=0.05,
        heartbeat_failures=2,
        backoff=(30.0,),
        on_crash=crashes.append,
    ) as (supervisor, consumer):

        async def scenario() -> None:
            await supervisor.start(timeout=5.0)
            consumer.drop_pongs.set()  # 模拟命令循环卡死：进程活着但不再回 PONG
            assert await _wait_state(supervisor, CoreState.CRASHED, timeout=5.0), (
                supervisor.state,
                consumer.counts,
            )
            record = supervisor.last_crash
            assert record is not None
            assert record["reason"] == "heartbeat_timeout"
            assert "exitcode" in record and record["exitcode"] is None  # 进程还活着
            assert record["at"] > 0
            assert record["recent_events"], record
            assert any(item["type"] == "READY" for item in record["recent_events"])
            assert len(crashes) == 1 and crashes[0] == record

            pid = supervisor.pid
            await asyncio.sleep(0.3)  # 退避 30 秒：不会自动重启
            assert supervisor.state is CoreState.CRASHED
            assert supervisor.pid == pid

        asyncio.run(scenario())
        # 判定失联后 stop() 仍能收掉这个（可能已卡死的）子进程，无残留。
        asyncio.run(supervisor.stop())
        assert supervisor.state is CoreState.STOPPED
        assert supervisor.exitcode is not None


# ---------------------------------------------------------------------------
# ③ 启动期 FATAL → FAILED + AppError(CORE_START_FAILED)
# ---------------------------------------------------------------------------


def test_startup_fatal_raises_start_failed_and_marks_failed() -> None:
    crashes: list[dict] = []
    config = _boot_config()
    config["asst_factory"] = "tests.fakes.fake_asst:NoSuchFactory"  # worker 启动期 FATAL
    supervisor = CoreSupervisor(
        config, heartbeat_interval=0.05, heartbeat_failures=2, on_crash=crashes.append
    )
    consumer = _Consumer(supervisor)
    consumer.start()
    supervisor.set_dispatcher(lambda event: None)
    try:
        with pytest.raises(AppError) as excinfo:
            asyncio.run(supervisor.start(timeout=5.0))
        assert excinfo.value.code is ErrorCode.CORE_START_FAILED
        assert supervisor.state is CoreState.FAILED
        record = supervisor.last_crash
        assert record is not None
        assert record["reason"] in ("fatal", "process_exit")
        assert len(crashes) == 1
        assert _wait_count(consumer, "FATAL", 1), consumer.counts
    finally:
        asyncio.run(_stop_supervisor(supervisor))
        consumer.close()


# ---------------------------------------------------------------------------
# ④ 退避序列与失败上限
# ---------------------------------------------------------------------------


def test_backoff_sequence_then_failed_after_budget() -> None:
    crashes: list[dict] = []
    transitions: list[tuple[CoreState, float]] = []
    with _running(
        backoff=(0.05, 0.1, 0.2),
        max_restart_attempts=3,
        on_crash=crashes.append,
        on_state_change=lambda state: transitions.append((state, time.monotonic())),
    ) as (supervisor, consumer):

        async def scenario() -> None:
            await supervisor.start(timeout=5.0)
            pids = [supervisor.pid]
            for index in range(4):
                old_pid = supervisor.pid
                os.kill(old_pid, signal.SIGKILL)  # 硬崩溃：exitcode = -9
                if index < 3:
                    ok = await _wait_new_ready(supervisor, old_pid, timeout=5.0)
                else:
                    ok = await _wait_state(supervisor, CoreState.FAILED, timeout=5.0)
                assert ok, (index, supervisor.state, consumer.counts, transitions)
                if index < 3:
                    pids.append(supervisor.pid)

            await asyncio.sleep(0.5)  # 超过最大退避 0.2s：确认不再 spawn
            assert supervisor.state is CoreState.FAILED
            assert supervisor.pid == pids[-1]
            assert supervisor.exitcode == -9  # SIGKILL（负值 = 信号杀死）
            assert [record["attempt"] for record in crashes] == [1, 2, 3, 4]
            assert all(record["exitcode"] == -9 for record in crashes)
            assert len(set(pids)) == 4  # 三次自动重启各换了新进程

        asyncio.run(scenario())

        delays = [
            restart_at - crash_at
            for crash_at, restart_at in zip(
                [at for state, at in transitions if state is CoreState.CRASHED],
                [at for state, at in transitions if state is CoreState.RESTARTING],
            )
        ]
        assert len(delays) == 3, transitions
        assert 0.0 <= delays[0] < 0.15, delays
        assert 0.04 <= delays[1] < 0.4, delays
        assert 0.1 <= delays[2] < 0.7, delays
        assert delays[0] < delays[2], delays


# ---------------------------------------------------------------------------
# ⑤ 长命令期间放宽心跳
# ---------------------------------------------------------------------------


def test_long_command_suspends_heartbeat_until_released() -> None:
    with _running(
        heartbeat_interval=0.05,
        heartbeat_failures=2,
        backoff=(30.0,),
    ) as (supervisor, consumer):

        async def scenario() -> None:
            await supervisor.start(timeout=5.0)
            consumer.drop_pongs.set()  # 收不到 PONG，正常情况下 0.1s 内就会判失联
            command = make_command("LOAD_RESOURCE", path="resource", incremental_paths=[])
            supervisor.track_command(command)

            await asyncio.sleep(0.4)  # 8 个心跳周期 ≫ 阈值，但不该累计失败
            assert supervisor.state is CoreState.READY, (supervisor.state, supervisor.last_crash)
            assert supervisor.last_crash is None

            supervisor.release_command(command["cmd_id"])
            assert await _wait_state(supervisor, CoreState.CRASHED, timeout=5.0), (
                supervisor.state,
                consumer.counts,
            )
            assert supervisor.last_crash is not None
            assert supervisor.last_crash["reason"] == "heartbeat_timeout"

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ⑥ 维护窗口：不判崩溃、不重启、退出后恢复
# ---------------------------------------------------------------------------


def test_maintenance_window_suppresses_crash_and_restores() -> None:
    crashes: list[dict] = []
    with _running(
        backoff=(0.1,), max_restart_attempts=3, on_crash=crashes.append
    ) as (supervisor, consumer):

        async def scenario() -> None:
            await supervisor.start(timeout=5.0)
            pid_before = supervisor.pid
            async with supervisor.acquire_maintenance():
                assert supervisor.in_maintenance is True
                os.kill(supervisor.pid, signal.SIGKILL)
                await asyncio.sleep(0.4)  # 远大于存活轮询间隔：窗口内不得有任何反应
                assert supervisor.state is CoreState.READY, supervisor.state
                assert crashes == []
                assert supervisor.pid == pid_before

            assert supervisor.in_maintenance is False
            # 退出窗口后恢复崩溃检测：窗口内的 kill 被补记为崩溃并自动重启。
            assert await _wait_new_ready(supervisor, pid_before, timeout=5.0), (
                supervisor.state,
                crashes,
            )
            assert supervisor.state is CoreState.READY
            assert len(crashes) == 1
            assert crashes[0]["reason"] == "process_exit"
            assert crashes[0]["exitcode"] == -9

        asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ⑦ 手动 restart(boot_config=...) 与钩子健壮性
# ---------------------------------------------------------------------------


def test_restarted_worker_still_receives_commands() -> None:
    """回归：崩溃重启后必须换新队列，否则新子进程会卡在旧队列的内部读锁上。

    ``Queue.get(timeout=None)`` 在阻塞期间持有内部读锁；被 SIGKILL 的子进程不会
    释放它。复用同一对 Queue 时，重启后的 worker 能上报 READY 却永远收不到命令。
    """
    with _running(backoff=(0.05,)) as (supervisor, consumer):

        async def scenario() -> None:
            await supervisor.start(timeout=5.0)
            first_pid = supervisor.pid
            os.kill(first_pid, signal.SIGKILL)
            assert await _wait_new_ready(supervisor, first_pid, timeout=5.0), supervisor.state

            command = make_command("GET_VERSION")
            supervisor.track_command(command)
            supervisor.cmd_queue.put(command)
            assert await _wait_cmd_result(consumer, command["cmd_id"], timeout=5.0), (
                consumer.counts,
                consumer.cmd_results,
            )
            assert consumer.results_for(command["cmd_id"])[0]["ok"] is True

        asyncio.run(scenario())


def test_manual_restart_swaps_boot_config_and_resets_budget() -> None:
    crashes: list[dict] = []
    with _running(
        backoff=(30.0,), max_restart_attempts=5, on_crash=crashes.append
    ) as (supervisor, consumer):

        async def scenario() -> None:
            await supervisor.start(timeout=5.0)
            first_pid = supervisor.pid
            os.kill(first_pid, signal.SIGKILL)
            assert await _wait_state(supervisor, CoreState.CRASHED, timeout=5.0), supervisor.state
            assert crashes[0]["attempt"] == 1  # 退避 30s：第 1 次失败后停在这里等手动触发

            await supervisor.restart(boot_config=_boot_config("success"))
            assert supervisor.state is CoreState.READY
            assert supervisor.pid != first_pid
            assert len(crashes) == 1  # 手动重启不算崩溃

            second_pid = supervisor.pid
            os.kill(second_pid, signal.SIGKILL)
            assert await _wait_state(supervisor, CoreState.CRASHED, timeout=5.0), supervisor.state
            assert crashes[1]["attempt"] == 1  # 退避预算已复位

        asyncio.run(scenario())


def test_on_crash_hook_exception_is_swallowed() -> None:
    def exploding_hook(record: dict) -> None:
        raise RuntimeError("钩子必须被吞掉")

    with _running(backoff=(30.0,), on_crash=exploding_hook) as (supervisor, consumer):

        async def scenario() -> None:
            await supervisor.start(timeout=5.0)
            os.kill(supervisor.pid, signal.SIGKILL)
            assert await _wait_state(supervisor, CoreState.CRASHED, timeout=5.0), supervisor.state
            assert supervisor.last_crash is not None

        asyncio.run(scenario())


def test_maintenance_window_is_mutually_exclusive() -> None:
    supervisor = CoreSupervisor(_boot_config())
    order: list[str] = []

    async def other() -> None:
        async with supervisor.acquire_maintenance():
            order.append("second")

    async def scenario() -> None:
        async with supervisor.acquire_maintenance():
            order.append("first-in")
            task = asyncio.create_task(other())
            await asyncio.sleep(0.1)  # 第二个进入者必须阻塞在锁上
            assert order == ["first-in"], order
            order.append("first-out")
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(scenario())
    assert order == ["first-in", "first-out", "second"]
    assert supervisor.in_maintenance is False
