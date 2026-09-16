"""崩溃恢复故障注入测试（M1-10；docs/03 §8 / §4.1–4.3、docs/13 §2）。

全链路走真实 IPC：spawn 出的子进程跑真实 worker 命令循环，唯一替换的是
``boot_config["asst_factory"]``（``tests.fakes.fake_asst:FakeAsst``）与剧本。
``CoreClient`` 是 ``event_queue`` 的唯一消费者（``set_dispatcher`` 由它声明），
本文件不另起消费者线程，因此同时验证 M1-08 与 M1-09 的接线。

覆盖 docs/03 §8 要求的「崩溃恢复故障注入」：crash 剧本在 ``start()`` 里发出首个
事件后 ``os._exit(-11)`` 模拟段错误（M1-05 交付，本卡不修改 FakeAsst），验证
CoreSupervisor 的检测、崩溃现场（``last_crash``）、退避重启、失败上限转 FAILED、
维护窗口互斥，以及换 success 剧本后的恢复成功路径。

**实测口径（M1-10 实测，Python 3.13.3 / macOS；已记入 .refactor/ENVIRONMENT.md）**：

1. ``os._exit(-11)`` 的退出状态只保留低 8 位，父进程 ``Process.exitcode`` 观测到的
   是 **245**（``-11 & 0xFF``），既不是 -11，也不是信号杀死（负值）。docs/03 §8 与
   任务卡写的 ``last_crash["exitcode"] == -11`` 是文档推测，实测优先：本文件按
   :data:`OBSERVED_CRASH_EXITCODE` 断言，并把注入值 :data:`INJECTED_CRASH_EXITCODE`
   保留在断言里，避免照文档推测写出永远失败的用例。
2. ``os._exit`` 不跑任何清理，会连带丢掉子进程 Queue feeder 线程尚未 flush 的事件：
   crash 剧本自己那条首个 CALLBACK 通常到不了父进程。因此「崩溃现场保留最近 N 条
   CALLBACK」这条 docs/03 §4.2 契约由
   :func:`test_crash_scene_keeps_recent_callback_events` 用「先产出回调、再 SIGKILL」
   的确定性路径覆盖，而不是依赖 crash 剧本的时序。
3. **M1-08 缺陷（实测，源文件归 M1-08，本卡不改）**：``restart()`` 在旧子进程仍存活时
   不会停掉上一代的存活监控线程。旧 liveness 线程持有旧 ``Process`` 引用，把这次
   **优雅停止**误判为崩溃（``process_exit`` / ``exitcode=0``）并调度自动重启；这个伪
   重启会 ``terminate()`` 掉刚起来的新子进程（实测 ``CORE_START_FAILED: ... exitcode=-15``），
   或至少多换一代进程并留下一条伪崩溃记录。包在 ``acquire_maintenance()`` 里也一样：
   窗口内 ``_handle_crash`` 被抑制但旧线程只是 ``reported=True`` 继续轮询，窗口一退出
   就补记伪崩溃。因此本文件的「恢复成功路径」（用例 ④）在 ``CRASHED`` 态手动
   ``restart(boot_config=...)``（旧进程已死、旧 liveness 线程已退出，无竞态）；
   该缺陷由 :func:`test_restart_from_ready_records_no_spurious_crash` 以非严格 xfail
   钉住，等 M1-08 修复后转正。

纪律：所有等待 ≤ :data:`WAIT_TIMEOUT`（5 秒），失败信息里打印已收到的事件与当前
状态（:meth:`_Harness.diag`）；每个用例结束都 ``stop(graceful=False)`` 并在 autouse
fixture 里兜底回收 ``maa-core`` 子进程；退避只注入毫秒级序列，绝不真跑默认 5/15/60 秒。
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import signal
import time
from typing import Any, Iterator, Optional

import pytest

from maa_api.core.client import CoreClient
from maa_api.core.enums import Message
from maa_api.core.supervisor import CRASH_EVENT_BUFFER, CoreState, CoreSupervisor
from tests.fakes.fake_asst import CRASH_SCRIPT, SCRIPTS

#: 所有等待的上限（秒）：用例纪律要求 ≤5 秒。
WAIT_TIMEOUT = 5.0

#: crash 剧本注入的退出码（SIGSEGV 语义）：``FakeAsst`` 里的 ``os._exit(-11)``。
INJECTED_CRASH_EXITCODE = -11

#: 父进程实际观测到的 exitcode：POSIX 退出状态只保留低 8 位 ⇒ ``-11 & 0xFF == 245``。
#: **不是 -11**（M1-10 实测），也不是信号杀死那种负值。
OBSERVED_CRASH_EXITCODE = INJECTED_CRASH_EXITCODE & 0xFF


def _boot_config(script: str) -> dict:
    """最小 boot_config：FakeAsst 不读文件系统、不加载动态库。"""
    return {
        "maa_path": ".",
        "user_dir": None,
        "incremental_paths": [],
        "instance_options": {},
        "asst_factory": "tests.fakes.fake_asst:FakeAsst",
        "asst_factory_kwargs": {"script": script},
    }


class _Harness:
    """supervisor + CoreClient + 观测记录，保证退出时不留子进程。

    观测钩子在构造时接好：``on_crash`` / ``on_state_change`` 进本对象的列表，
    ``CoreClient`` 的分派表额外记录 READY / CMD_RESULT / CALLBACK / LOG / FATAL，
    供 :meth:`diag` 在断言失败时打印「已收到的事件与当前状态」。
    """

    def __init__(self, script: str = SCRIPTS["crash"].name, **supervisor_kwargs: Any) -> None:
        self.crashes: list[dict] = []
        self.transitions: list[tuple[CoreState, float]] = []
        self.received: list[str] = []
        self.callback_msgs: list[int] = []
        self.supervisor = CoreSupervisor(
            _boot_config(script),
            on_crash=self.crashes.append,
            on_state_change=lambda state: self.transitions.append((state, time.monotonic())),
            **supervisor_kwargs,
        )
        self.client = CoreClient(self.supervisor)
        for kind in ("READY", "CMD_RESULT", "FATAL", "LOG", "PONG"):
            self.client.on(kind, self._recorder(kind))
        self.client.on("CALLBACK", self._record_callback)

    # ---- 观测 ----

    def _recorder(self, kind: str):
        def handler(_payload: dict) -> None:
            self.received.append(kind)

        return handler

    def _record_callback(self, payload: dict) -> None:
        msg = payload.get("msg")
        self.callback_msgs.append(int(msg) if isinstance(msg, int) else -1)
        self.received.append(f"CALLBACK:{msg}")

    def diag(self, note: str = "") -> str:
        """失败信息：已收到的事件 + 当前状态 + 崩溃记录（保证超时可见原因）。"""
        summary = [
            (record["attempt"], record["exitcode"], record["reason"])
            for record in self.crashes
        ]
        return (
            f"{note} | state={self.supervisor.state} pid={self.supervisor.pid} "
            f"exitcode={self.supervisor.exitcode} "
            f"in_maintenance={self.supervisor.in_maintenance} "
            f"crashes(attempt,exitcode,reason)={summary} "
            f"transitions={[state.value for state, _ in self.transitions]} "
            f"events={self.received} last_crash={self.supervisor.last_crash}"
        )

    # ---- 等待（全部 ≤5 秒，超时返回 False，由调用方断言并打印 diag） ----

    async def wait_state(self, state: CoreState, timeout: float = WAIT_TIMEOUT) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.supervisor.state is state:
                return True
            await asyncio.sleep(0.01)
        return self.supervisor.state is state

    async def wait_ready(
        self, *, old_pid: Optional[int] = None, timeout: float = WAIT_TIMEOUT
    ) -> bool:
        """等 READY；给了 ``old_pid`` 时还要求 pid 已变（避免读到崩溃前的旧 READY）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.supervisor.state is CoreState.READY and (
                old_pid is None or self.supervisor.pid != old_pid
            ):
                return True
            await asyncio.sleep(0.01)
        return False

    async def wait_crashes(self, count: int, timeout: float = WAIT_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.crashes) >= count:
                return True
            await asyncio.sleep(0.01)
        return len(self.crashes) >= count

    async def wait_callback(self, msg: Message, timeout: float = WAIT_TIMEOUT) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if int(msg.value) in self.callback_msgs:
                return True
            await asyncio.sleep(0.01)
        return int(msg.value) in self.callback_msgs

    # ---- 动作 ----

    async def trigger_crash(self) -> None:
        """APPEND_TASK + START 触发 crash 剧本的 ``os._exit``。

        crash 剧本在 ``start()`` 里直接 ``os._exit``，START 永远等不到 CMD_RESULT，
        所以 START 以 task 形式下发，等崩溃被记录后再取消（不白等 30 秒超时）。
        """
        before = len(self.crashes)
        task_id = await self.client.append_task("StartUp", {"client_type": "Official"})
        assert task_id > 0, self.diag(f"APPEND_TASK 应返回非零 task_id，实际 {task_id!r}")
        start_task = asyncio.create_task(self.client.start())
        try:
            assert await self.wait_crashes(before + 1), self.diag("START 后未检测到崩溃")
        finally:
            start_task.cancel()
            with contextlib.suppress(BaseException):
                await start_task

    # ---- 生命周期 ----

    async def __aenter__(self) -> "_Harness":
        # 必须在事件循环内启动消费线程：CoreClient 立刻绑定 loop 并声明唯一消费者。
        self.client.start_consumer()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self.supervisor.stop(graceful=False, timeout=2.0), timeout=WAIT_TIMEOUT
            )
        self.client.close()


@pytest.fixture(autouse=True)
def _reap_core_children() -> Iterator[None]:
    """兜底：任何用例结束后都不允许残留 maa-core 子进程。"""
    yield
    for child in multiprocessing.active_children():
        if child.name == "maa-core" and child.is_alive():
            child.kill()
            child.join(timeout=2.0)


# ---------------------------------------------------------------------------
# ① 段错误注入 → 检测 + 崩溃现场 + on_crash 钩子
# ---------------------------------------------------------------------------


def test_crash_script_segfault_detected_with_scene_and_hook() -> None:
    async def scenario() -> None:
        # 退避 30 秒：崩溃后停在 CRASHED，便于观察现场而不被自动重启冲掉。
        async with _Harness(script=SCRIPTS["crash"].name, backoff=(30.0,)) as core:
            await core.supervisor.start(timeout=WAIT_TIMEOUT)
            assert core.supervisor.state is CoreState.READY, core.diag("启动后应为 READY")
            first_pid = core.supervisor.pid
            assert isinstance(first_pid, int)
            assert CRASH_SCRIPT.crash_exitcode == INJECTED_CRASH_EXITCODE

            await core.trigger_crash()

            assert core.supervisor.state is CoreState.CRASHED, core.diag("崩溃后应停在 CRASHED")
            assert core.supervisor.pid == first_pid, core.diag("CRASHED 阶段不得提前换进程")
            record = core.supervisor.last_crash
            assert record is not None, core.diag("崩溃后必须有 last_crash")
            assert record["reason"] == "process_exit", core.diag("硬崩溃的判据是进程退出")
            assert record["exitcode"] == OBSERVED_CRASH_EXITCODE, core.diag(
                "os._exit(-11) 实测观测为 245（-11 & 0xFF），不是 -11"
            )
            assert OBSERVED_CRASH_EXITCODE == 245, OBSERVED_CRASH_EXITCODE
            assert core.supervisor.exitcode == OBSERVED_CRASH_EXITCODE
            assert record["pid"] == first_pid and record["attempt"] == 1
            assert record["at"] > 0, core.diag("崩溃现场必须带时间戳")

            # 崩溃现场：最近事件环形缓冲（READY / APPEND_TASK 的 CMD_RESULT 都在里面）。
            scene = record["recent_events"]
            assert scene, core.diag("崩溃现场 recent_events 不得为空")
            assert len(scene) <= CRASH_EVENT_BUFFER
            assert any(event["type"] == "READY" for event in scene), scene
            assert any(event["type"] == "CMD_RESULT" for event in scene), scene
            # crash 剧本自己那条 CALLBACK 会被 os._exit 丢在 feeder 缓冲里（实测），
            # CALLBACK 进现场的契约由 test_crash_scene_keeps_recent_callback_events 覆盖。

            # 钩子只在检测线程上同步调用一次，且收到的 record 就是 last_crash。
            assert len(core.crashes) == 1, core.diag("on_crash 必须恰好调用一次")
            assert core.crashes[0] == record, core.diag("钩子收到的 record 应与 last_crash 一致")

            await asyncio.sleep(0.3)  # 远小于 30 秒退避：不应自动重启
            assert core.supervisor.state is CoreState.CRASHED, core.diag("退避未到不得重启")
            assert core.supervisor.pid == first_pid, core.diag("退避未到不得 spawn 新进程")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ② 退避序列按注入值增长 + 连续失败达上限转 FAILED 后不再 spawn
# ---------------------------------------------------------------------------


def test_backoff_sequence_grows_then_failed_after_budget() -> None:
    async def scenario() -> None:
        async with _Harness(
            script=SCRIPTS["crash"].name,
            backoff=(0.05, 0.1, 0.2),
            max_restart_attempts=3,
        ) as core:
            await core.supervisor.start(timeout=WAIT_TIMEOUT)
            pids = [core.supervisor.pid]

            # max_restart_attempts=3：前 3 次崩溃各换一代新进程，第 4 次转 FAILED。
            for index in range(4):
                old_pid = core.supervisor.pid
                await core.trigger_crash()
                if index < 3:
                    assert await core.wait_ready(old_pid=old_pid), core.diag(
                        f"第 {index + 1} 次崩溃后未自动重启到 READY"
                    )
                    pids.append(core.supervisor.pid)
                else:
                    assert await core.wait_state(CoreState.FAILED), core.diag(
                        "预算耗尽后应停在 FAILED"
                    )

            await asyncio.sleep(0.5)  # > 最大退避 0.2 秒：确认不再自动重启
            assert core.supervisor.state is CoreState.FAILED, core.diag("FAILED 后不得再重启")
            assert core.supervisor.pid == pids[-1], core.diag("FAILED 后不得 spawn 新进程")
            assert core.supervisor.exitcode == OBSERVED_CRASH_EXITCODE
            assert [record["attempt"] for record in core.crashes] == [1, 2, 3, 4], core.diag(
                "退避预算必须跨自动重启累计，手动 start/restart 才复位"
            )
            assert all(
                record["exitcode"] == OBSERVED_CRASH_EXITCODE for record in core.crashes
            ), core.diag("每次崩溃现场都要带 exitcode")
            assert len(set(pids)) == 4, core.diag("三次自动重启各应换一代新进程")

            # 重启间隔按注入序列增长：用状态钩子的时间戳量 CRASHED → RESTARTING。
            delays = [
                restart_at - crash_at
                for crash_at, restart_at in zip(
                    [at for state, at in core.transitions if state is CoreState.CRASHED],
                    [at for state, at in core.transitions if state is CoreState.RESTARTING],
                )
            ]
            assert len(delays) == 3, core.diag(f"应有 3 次自动重启，实测间隔 {delays}")
            assert 0.03 <= delays[0] < 0.5, (delays, core.diag("第 1 档退避 0.05s"))
            assert 0.08 <= delays[1] < 0.9, (delays, core.diag("第 2 档退避 0.1s"))
            assert 0.15 <= delays[2] < 1.5, (delays, core.diag("第 3 档退避 0.2s"))
            assert delays[0] < delays[1] < delays[2], (delays, core.diag("退避必须递增"))

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ③ 维护窗口互斥：窗口内「停止」不是崩溃；退出后检测与重启恢复
# ---------------------------------------------------------------------------


def test_maintenance_window_suppresses_crash_then_recovers() -> None:
    async def scenario() -> None:
        async with _Harness(
            script=SCRIPTS["crash"].name, backoff=(0.05,), max_restart_attempts=3
        ) as core:
            await core.supervisor.start(timeout=WAIT_TIMEOUT)
            pid_before = core.supervisor.pid

            async with core.supervisor.acquire_maintenance():
                assert core.supervisor.in_maintenance is True, core.diag("应处于维护窗口")
                os.kill(core.supervisor.pid, signal.SIGKILL)  # 窗口内的「停止」是预期行为
                await asyncio.sleep(0.4)  # 8 倍存活轮询间隔：窗口内不得有任何反应
                assert core.supervisor.state is CoreState.READY, core.diag(
                    "维护窗口内的停止不得判崩溃"
                )
                assert core.crashes == [], core.diag("维护窗口内不得调用 on_crash")
                assert core.supervisor.last_crash is None, core.diag("不得留下崩溃记录")
                assert core.supervisor.pid == pid_before, core.diag("窗口内不得 spawn 新进程")

            assert core.supervisor.in_maintenance is False
            # 退出窗口：窗口内死掉的子进程被补记为崩溃（-9 = SIGKILL 真信号），自动重启生效。
            assert await core.wait_crashes(1), core.diag("退出窗口后崩溃恢复未生效")
            assert core.crashes[0]["reason"] == "process_exit"
            assert core.crashes[0]["exitcode"] == -9, core.diag("SIGKILL 的 exitcode 是 -9")
            assert core.crashes[0]["exitcode"] < 0, "真信号杀死给出负 exitcode"
            assert await core.wait_ready(old_pid=pid_before), core.diag(
                "退出维护窗口后未自动重启到 READY"
            )

            # 再制造一次脚本崩溃：恢复后的检测 → 退避 → 重启链路重新生效。
            second_pid = core.supervisor.pid
            await core.trigger_crash()
            assert len(core.crashes) == 2, core.diag("退出窗口后第二次崩溃未被记录")
            assert core.crashes[1]["attempt"] == 2, core.diag("自动重启预算应继续累计")
            assert await core.wait_ready(old_pid=second_pid), core.diag(
                "第二次崩溃后未自动重启"
            )
            assert core.supervisor.state is CoreState.READY

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ④ 恢复成功路径：crash 失败若干次后手动换 success 剧本 → READY、预算复位
# ---------------------------------------------------------------------------


def test_manual_restart_with_success_script_recovers_and_resets_budget() -> None:
    async def scenario() -> None:
        # backoff 第 1 档 0.05s：第一次崩溃由自动重启兜住；第 2 档 30s：第二次崩溃停在
        # CRASHED 等手动 restart（旧进程已死、旧 liveness 线程已退出，避开 M1-08 的
        # restart 竞态，见模块 docstring 第 3 条）。
        async with _Harness(
            script=SCRIPTS["crash"].name, backoff=(0.05, 30.0), max_restart_attempts=5
        ) as core:
            await core.supervisor.start(timeout=WAIT_TIMEOUT)

            # 失败 1：短退避自动重启兜住；自动重启成功不复位预算。
            first_pid = core.supervisor.pid
            await core.trigger_crash()
            assert await core.wait_ready(old_pid=first_pid), core.diag(
                "自动重启未回到 READY"
            )
            assert [record["attempt"] for record in core.crashes] == [1], core.diag(
                "自动重启成功不得复位退避预算"
            )

            # 失败 2：退避 30 秒，停在 CRASHED 等手动 restart。
            second_pid = core.supervisor.pid
            await core.trigger_crash()
            assert await core.wait_state(CoreState.CRASHED), core.diag(
                "第二次崩溃应停在 CRASHED"
            )
            assert [record["attempt"] for record in core.crashes] == [1, 2], core.diag(
                "退避预算必须跨自动重启累计"
            )

            # 手动 restart 同时换 boot_config（success 剧本）并复位退避预算。
            await core.supervisor.restart(boot_config=_boot_config("success"))
            assert core.supervisor.state is CoreState.READY, core.diag(
                "换 success 剧本后应回到 READY"
            )
            assert core.supervisor.pid != second_pid, core.diag("手动 restart 必须换新进程")
            assert len(core.crashes) == 2, core.diag("手动 restart 不是崩溃，不得新增记录")

            # 新一代真的能干活：换队列后命令与回调都还通（回归 M1-08 的队列轮换）。
            task_id = await core.client.append_task("StartUp", {"client_type": "Official"})
            assert task_id > 0, core.diag(f"恢复后 APPEND_TASK 返回 {task_id!r}")
            assert await core.client.start() is True, core.diag("success 剧本的 START 应回执 True")
            assert await core.wait_callback(Message.TaskChainStart), core.diag("未收到开始回调")
            assert await core.wait_callback(Message.TaskChainCompleted), core.diag(
                "未收到完成回调"
            )

            # 退避预算复位：手动 restart 之后的下一次崩溃重新从 attempt=1 计。
            last_pid = core.supervisor.pid
            os.kill(last_pid, signal.SIGKILL)
            assert await core.wait_crashes(3), core.diag("SIGKILL 未被检测")
            assert core.crashes[2]["attempt"] == 1, core.diag("手动 restart 后预算未复位")
            assert core.crashes[2]["exitcode"] == -9

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ⑤ 崩溃现场保留最近的 CALLBACK 事件（docs/03 §4.2）
# ---------------------------------------------------------------------------


def test_crash_scene_keeps_recent_callback_events() -> None:
    """先让 success 剧本产出回调并被消费，再 SIGKILL：现场必须留下这些 CALLBACK。

    crash 剧本的 ``os._exit`` 会丢掉 feeder 缓冲里的事件（实测），所以 CALLBACK
    进现场的契约用这条确定性路径验证，而不是依赖崩溃注入的时序。
    """

    async def scenario() -> None:
        async with _Harness(script="success", backoff=(30.0,)) as core:
            await core.supervisor.start(timeout=WAIT_TIMEOUT)
            await core.client.append_task("StartUp", {"client_type": "Official"})
            assert await core.client.start() is True, core.diag("success 的 START 应回执 True")
            assert await core.wait_callback(Message.TaskChainCompleted), core.diag(
                "未收到 TaskChainCompleted"
            )

            os.kill(core.supervisor.pid, signal.SIGKILL)
            assert await core.wait_crashes(1), core.diag("SIGKILL 未被检测")
            record = core.supervisor.last_crash
            assert record is not None, core.diag("崩溃后必须有 last_crash")
            assert record["exitcode"] == -9, core.diag("SIGKILL 的 exitcode 是 -9")

            scene = record["recent_events"]
            assert len(scene) <= CRASH_EVENT_BUFFER
            assert any(event["type"] == "READY" for event in scene), scene
            callback_events = [event for event in scene if event["type"] == "CALLBACK"]
            assert callback_events, core.diag("崩溃现场必须保留最近的 CALLBACK 事件")
            msgs = [event["payload"].get("msg") for event in callback_events]
            assert Message.TaskChainStart.value in msgs, (msgs, core.diag("缺少开始回调"))
            assert Message.TaskChainCompleted.value in msgs, (msgs, core.diag("缺少完成回调"))

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ⑥ 缺陷钉住（xfail）：维护窗口内的 restart 不得留下伪崩溃
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=False,
    reason=(
        "M1-08 缺陷（M1-10 实测，源文件归 M1-08）：restart() 在旧进程存活时不停上一代"
        "存活监控线程，旧 liveness 线程把优雅停止误判为 process_exit/exitcode=0 并调度"
        "自动重启；即使包在 acquire_maintenance() 里，窗口退出后也会补记伪崩溃（并可能"
        "terminate() 掉刚起来的新进程）。修复后本用例应转正（去掉 xfail）。"
    ),
)
def test_restart_from_ready_records_no_spurious_crash() -> None:
    """docs/03 §4.3：维护窗口内的「停止」是预期行为 —— 不记崩溃、不触发重启。

    这是 ``restart()`` 的文档规定用法（热更新 / 重装内核 / 改 MAA 路径）。M1-10 实测
    它仍会留下伪崩溃记录，故钉成非严格 xfail：现在是 xfailed，M1-08 修复后是 xpass，
    两种情况都不会让套件变红。
    """

    async def scenario() -> None:
        async with _Harness(script=SCRIPTS["crash"].name, backoff=(0.05,)) as core:
            await core.supervisor.start(timeout=WAIT_TIMEOUT)
            async with core.supervisor.acquire_maintenance():
                await core.supervisor.restart(boot_config=_boot_config("success"))
                assert core.supervisor.state is CoreState.READY, core.diag(
                    "维护窗口内 restart 应回到 READY"
                )

            restarted_pid = core.supervisor.pid
            assert core.crashes == [], core.diag("维护窗口内的优雅停止不得记为崩溃")
            await asyncio.sleep(0.3)  # 让残留的旧 liveness 线程有机会误报
            assert core.crashes == [], core.diag("退出窗口后出现伪崩溃记录")
            assert core.supervisor.pid == restarted_pid, core.diag("健康进程被伪崩溃重启")
            assert core.supervisor.state is CoreState.READY, core.diag("健康进程应为 READY")

    asyncio.run(scenario())
